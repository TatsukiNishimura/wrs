"""
Data driven ik solver
author: weiwei
date: 20231107
"""
import warnings
import os
import numpy as np
import pickle
import basis.robot_math as rm
import basis.utils as bu
import scipy.spatial
from scipy.spatial.transform import Rotation
from tqdm import tqdm
import robot_sim._kinematics.ik_num as rkn
import robot_sim._kinematics.ik_opt as rko
import robot_sim._kinematics.ik_trac as rkt
import random
import time
# for debugging purpose
import modeling.geometric_model as mgm
import robot_sim._kinematics.model_generator as rkmg
import basis.constant as bc10


class DDIKSolver(object):
    def __init__(self, jlc, path=None, identifier_str='test', backbone_solver='n', rebuild=False):
        """
        :param jlc:
        :param path:
        :param backbone_solver: 'n': num ik; 'o': opt ik; 't': trac ik
        :param rebuild:
        author: weiwei
        date: 20231111
        """
        self.jlc = jlc
        current_file_dir = os.path.dirname(__file__)
        if path is None:
            path = os.path.join(os.path.dirname(current_file_dir), "_data_files")
        self._fname_tree = os.path.join(path, f"{identifier_str}_ikdd_tree.pkl")
        self._fname_jnt = os.path.join(path, f"{identifier_str}_jnt_data.pkl")
        self._k_bbs = 2  # number of nearest neighbours examined by the backbone sovler
        self._k_max = 20  # maximum nearest neighbours explored by the evolver
        self._max_n_iter = 7  # max_n_iter of the backbone solver
        if backbone_solver == 'n':
            self._backbone_solver = rkn.NumIKSolver(self.jlc)
        elif backbone_solver == 'o':
            self._backbone_solver = rko.OptIKSolver(self.jlc)
        elif backbone_solver == 't':
            self._backbone_solver = rkt.TracIKSolver(self.jlc)
        if rebuild:
            print("Rebuilding the database. It starts a new evolution and is costly.")
            y_or_n = bu.get_yesno()
            if y_or_n == 'y':
                self.querry_tree, self.jnt_data = self._build_data()
                self.persist_data()
                self.evolve_data(n_times=100000)
        else:
            try:
                self.querry_tree = pickle.load(open(self._fname_tree, 'rb'))
                self.jnt_data = pickle.load(open(self._fname_jnt, 'rb'))
            except FileNotFoundError:
                self.querry_tree, self.jnt_data = self._build_data()
                self.persist_data()
                self.evolve_data(n_times=100)

    def __call__(self,
                 tgt_pos,
                 tgt_rotmat,
                 seed_jnt_values=None,
                 max_n_iter=None,
                 toggle_evolve=True,
                 toggle_dbg=False):
        """
        :param tgt_pos:
        :param tgt_rotmat:
        :param seed_jnt_values:
        :param max_n_iter: use self._max_n_iter if None
        :param toggle_evolve: do we update the database file
        :param toggle_dbg:
        :return:
        """
        return self.ik(tgt_pos=tgt_pos,
                       tgt_rotmat=tgt_rotmat,
                       seed_jnt_values=seed_jnt_values,
                       max_n_iter=max_n_iter,
                       toggle_evolve=toggle_evolve,
                       toggle_dbg=toggle_dbg)

    def _rotmat_to_vec(self, rotmat, method='q'):
        """
        convert a rotmat to vectors
        this will be used for computing the Minkowski p-norm required by KDTree query
        'f' or 'q' are recommended, they both have satisfying performance
        :param method: 'f': Frobenius; 'q': Quaternion; 'r': rpy; '-': same value
        :return:
        author: weiwei
        date: 20231107
        """
        if method == 'f':
            return rotmat.ravel()
        if method == 'q':
            return Rotation.from_matrix(rotmat).as_quat()
        if method == 'r':
            return rm.rotmat_to_euler(rotmat)
        if method == '-':
            return np.array([0])

    def _build_data(self):
        # gen sampled qs
        sampled_jnts = []
        n_intervals = np.linspace(8, 4, self.jlc.n_dof, endpoint=True)
        print(f"Buidling Data for DDIK using the following joint granularity: {n_intervals.astype(int)}...")
        for i in range(self.jlc.n_dof):
            sampled_jnts.append(
                np.linspace(self.jlc.jnt_ranges[i][0], self.jlc.jnt_ranges[i][1], int(n_intervals[i]), endpoint=False))
        grid = np.meshgrid(*sampled_jnts)
        sampled_qs = np.vstack([x.ravel() for x in grid]).T
        # gen sampled qs and their correspondent flange poses
        flange_data = []
        jnt_data = []
        for id in tqdm(range(len(sampled_qs))):
            jnt_values = sampled_qs[id]
            flange_pos, flange_rotmat = self.jlc.fk(jnt_values=jnt_values, toggle_jacobian=False)
            flange_rotvec = self._rotmat_to_vec(flange_rotmat)
            flange_data.append(np.concatenate((flange_pos, flange_rotvec)))
            jnt_data.append(jnt_values)
        querry_tree = scipy.spatial.cKDTree(flange_data)
        return querry_tree, jnt_data

    def multiepoch_evolve(self, n_times_per_epoch=10000, target_success_rate=.96):
        """
        calls evolve_data repeated based on user feedback
        :return:
        author: weiwei
        date: 20231111
        """
        print("Starting multi-epoch evolution.")
        current_success_rate = 0.0
        while current_success_rate < target_success_rate:
            self.evolve_data(n_times=n_times_per_epoch)
            current_success_rate = self.test_success_rate()
            # print("An epoch is done. Do you want to continue?")
            # y_or_n = bu.get_yesno()
            # if y_or_n == 'n':
            #     break
        self.persist_data()

    def evolve_data(self, n_times=100000, toggle_dbg=True):
        evolved_nns = []
        outer_progress_bar = tqdm(total=n_times, desc="new goals:", colour="red", position=0, leave=False)
        for i in range(n_times):
            outer_progress_bar.update(1)
            random_jnts = self.jlc.rand_conf()
            flange_pos, flange_rotmat = self.jlc.fk(jnt_values=random_jnts, update=False, toggle_jacobian=False)
            flange_wvec = self._rotmat_to_vec(flange_rotmat)
            query_point = np.concatenate((flange_pos, flange_wvec))
            dist_value_array, nn_indx_array = self.querry_tree.query(query_point, k=self._k_max, workers=-1)
            is_solvable = False
            for nn_indx in nn_indx_array[:self._k_bbs]:
                seed_jnt_values = self.jnt_data[nn_indx]
                result = self._backbone_solver(tgt_pos=flange_pos,
                                               tgt_rotmat=flange_rotmat,
                                               seed_jnt_values=seed_jnt_values,
                                               max_n_iter=self._max_n_iter)
                if result is None:
                    continue
                else:
                    is_solvable = True
                    break
            if not is_solvable:
                # try solving the problem with additional nearest neighbours
                # inner_progress_bar = tqdm(total=self._k_max - self._k_bbs,
                #                           desc="    unvolsed. try extra nns:",
                #                           colour="green",
                #                           position=1,
                #                           leave=False)
                for id, nn_indx in enumerate(nn_indx_array[self._k_bbs:]):
                    # inner_progress_bar.update(1)
                    seed_jnt_values = self.jnt_data[nn_indx]
                    result = self._backbone_solver(tgt_pos=flange_pos,
                                                   tgt_rotmat=flange_rotmat,
                                                   seed_jnt_values=seed_jnt_values,
                                                   max_n_iter=self._max_n_iter)
                    if result is None:
                        continue
                    else:
                        # if solved, add the new jnts to the data and update the kd tree
                        tree_data = np.vstack((self.querry_tree.data, query_point))
                        self.jnt_data.append(result)
                        self.querry_tree = scipy.spatial.cKDTree(tree_data)
                        evolved_nns.append(self._k_bbs + id)
                        print(f"#### Previously unsolved ik solved using the {self._k_bbs + id}th nearest neighbour.")
                        break
                # inner_progress_bar.close()
        outer_progress_bar.close()
        if toggle_dbg:
            print("+++++++++++++++++++evolution details+++++++++++++++++++")
            if len(evolved_nns) > 0:
                evolved_nns = np.asarray(evolved_nns)
                print("Max nn id: ", evolved_nns.max())
                print("Min nn id: ", evolved_nns.min())
                print("Avg nn id: ", evolved_nns.mean())
                print("Std nn id: ", evolved_nns.std())
            else:
                print("No successful evolution.")
        self.persist_data()

    def persist_data(self):
        pickle.dump(self.querry_tree, open(self._fname_tree, 'wb'))
        pickle.dump(self.jnt_data, open(self._fname_jnt, 'wb'))
        print("ddik data file saved.")

    def ik(self,
           tgt_pos,
           tgt_rotmat,
           seed_jnt_values=None,
           max_n_iter=None,
           toggle_evolve=True,
           toggle_dbg=False):
        """
        :param tgt_pos:
        :param tgt_rotmat:
        :param seed_jnt_values: ignored
        :param toggle_dbg: ignored
        :return:
        author: weiwei
        date: 20231107
        """
        max_n_iter = self._max_n_iter if max_n_iter is None else max_n_iter
        if seed_jnt_values is not None:
            return self._backbone_solver(tgt_pos=tgt_pos,
                                         tgt_rotmat=tgt_rotmat,
                                         seed_jnt_values=seed_jnt_values,
                                         max_n_iter=max_n_iter,
                                         toggle_dbg=toggle_dbg)
        else:
            tgt_wvec = self._rotmat_to_vec(tgt_rotmat)
            query_point = np.concatenate((tgt_pos, tgt_wvec))
            dist_value_array, nn_indx_array = self.querry_tree.query(query_point, k=self._k_max, workers=-1)
            for id, nn_indx in enumerate(nn_indx_array):
                seed_jnt_values = self.jnt_data[nn_indx]
                if toggle_dbg:
                    rkmg.gen_jlc_stick_by_jnt_values(self.jlc,
                                                     jnt_values=seed_jnt_values,
                                                     stick_rgba=rm.bc.red).attach_to(base)
                result = self._backbone_solver(tgt_pos=tgt_pos,
                                               tgt_rotmat=tgt_rotmat,
                                               seed_jnt_values=seed_jnt_values,
                                               max_n_iter=max_n_iter,
                                               toggle_dbg=toggle_dbg)
                if result is None:
                    # print(result)
                    # base.run()
                    # continue
                    if toggle_evolve:
                        continue
                    else:
                        return None
                else:
                    if id > self._k_bbs:
                        tree_data = np.vstack((self.querry_tree.data, query_point))
                        self.jnt_data.append(result)
                        self.querry_tree = scipy.spatial.cKDTree(tree_data)
                        print(f"Updating query tree, {id} explored...")
                        self.persist_data()
                        break
                    return result
            # failed to find a solution, use optimization methods to solve and update the database?
        return None

    def test_success_rate(self, n_times=100):
        success = 0
        time_list = []
        tgt_list = []
        for i in tqdm(range(n_times), desc="ik"):
            random_jnts = self.jlc.rand_conf()
            flange_pos, flange_rotmat = self.jlc.fk(jnt_values=random_jnts, update=False, toggle_jacobian=False)
            tic = time.time()
            solved_jnt_values = self.jlc.ik(tgt_pos=flange_pos,
                                            tgt_rotmat=flange_rotmat,
                                            # seed_jnt_values=seed_jnt_values,
                                            toggle_dbg=False)
            toc = time.time()
            time_list.append(toc - tic)
            if solved_jnt_values is not None:
                success += 1
            else:
                tgt_list.append((flange_pos, flange_rotmat))
        print("------------------testing results------------------")
        print(f"The current success rate is: {success / n_times * 100}%")
        print('average time cost', np.mean(time_list))
        print('max', np.max(time_list))
        print('min', np.min(time_list))
        print('std', np.std(time_list))
        return success / n_times


if __name__ == '__main__':
    import modeling.geometric_model as gm
    import robot_sim._kinematics.jlchain as rkjlc
    import time
    import math
    import robot_sim._kinematics.model_generator as rkmg
    import visualization.panda.world as wd

    base = wd.World(cam_pos=[1.25, .75, .75], lookat_pos=[0, 0, .3])
    gm.gen_frame().attach_to(base)

    _jnt_safemargin = math.pi / 18.0
    jlc = rkjlc.JLChain(n_dof=7)
    jlc.jnts[0].loc_pos = np.array([.0, .0, .0])
    jlc.jnts[0].loc_rotmat = rm.rotmat_from_euler(0.0, 0.0, np.pi)
    jlc.jnts[0].loc_motion_ax = np.array([0, 0, 1])
    jlc.jnts[0].motion_range = np.array([-2.94087978961 + _jnt_safemargin, 2.94087978961 - _jnt_safemargin])
    jlc.jnts[1].loc_pos = np.array([0.03, .0, .1])
    jlc.jnts[1].loc_rotmat = rm.rotmat_from_euler(np.pi / 2, 0.0, 0.0)
    jlc.jnts[1].loc_motion_ax = np.array([0, 0, 1])
    jlc.jnts[1].motion_range = np.array([-2.50454747661 + _jnt_safemargin, 0.759218224618 - _jnt_safemargin])
    jlc.jnts[2].loc_pos = np.array([-0.03, 0.17283, 0.0])
    jlc.jnts[2].loc_rotmat = rm.rotmat_from_euler(-np.pi / 2, 0.0, 0.0)
    jlc.jnts[2].loc_motion_ax = np.array([0, 0, 1])
    jlc.jnts[2].motion_range = np.array([-2.94087978961 + _jnt_safemargin, 2.94087978961 - _jnt_safemargin])
    jlc.jnts[3].loc_pos = np.array([-0.04188, 0.0, 0.07873])
    jlc.jnts[3].loc_rotmat = rm.rotmat_from_euler(np.pi / 2, -np.pi / 2, 0.0)
    jlc.jnts[3].loc_motion_ax = np.array([0, 0, 1])
    jlc.jnts[3].motion_range = np.array([-2.15548162621 + _jnt_safemargin, 1.3962634016 - _jnt_safemargin])
    jlc.jnts[4].loc_pos = np.array([0.0405, 0.16461, 0.0])
    jlc.jnts[4].loc_rotmat = rm.rotmat_from_euler(-np.pi / 2, 0.0, 0.0)
    jlc.jnts[4].loc_motion_ax = np.array([0, 0, 1])
    jlc.jnts[4].motion_range = np.array([-5.06145483078 + _jnt_safemargin, 5.06145483078 - _jnt_safemargin])
    jlc.jnts[5].loc_pos = np.array([-0.027, 0, 0.10039])
    jlc.jnts[5].loc_rotmat = rm.rotmat_from_euler(np.pi / 2, 0.0, 0.0)
    jlc.jnts[5].loc_motion_ax = np.array([0, 0, 1])
    jlc.jnts[5].motion_range = np.array([-1.53588974176 + _jnt_safemargin, 2.40855436775 - _jnt_safemargin])
    jlc.jnts[6].loc_pos = np.array([0.027, 0.029, 0.0])
    jlc.jnts[6].loc_rotmat = rm.rotmat_from_euler(-np.pi / 2, 0.0, 0.0)
    jlc.jnts[6].loc_motion_ax = np.array([0, 0, 1])
    jlc.jnts[6].motion_range = np.array([-3.99680398707 + _jnt_safemargin, 3.99680398707 - _jnt_safemargin])
    jlc._loc_flange_pos = np.array([0, 0, .007])
    jlc.finalize(ik_solver='d', identifier_str="test")

    goal_jnt_values = jlc.rand_conf()
    rkmg.gen_jlc_stick_by_jnt_values(jlc, jnt_values=goal_jnt_values, stick_rgba=rm.bc.blue).attach_to(base)

    tgt_pos, tgt_rotmat = jlc.fk(jnt_values=goal_jnt_values)
    tic = time.time()

    jnt_values = jlc.ik(tgt_pos=tgt_pos,
                        tgt_rotmat=tgt_rotmat,
                        toggle_dbg=False)
    toc = time.time()
    mgm.gen_frame(pos=tgt_pos, rotmat=tgt_rotmat).attach_to(base)
    print(toc - tic, jnt_values)
    base.run()
    if jnt_values is not None:
        jlc.goto_given_conf(jnt_values=jnt_values)
        rkmg.gen_jlc_stick(jlc, stick_rgba=rm.bc.navy_blue, toggle_flange_frame=True,
                           toggle_jnt_frames=False).attach_to(base)
        base.run()

    # jlc._ik_solver._test_success_rate()
    jlc._ik_solver.multiepoch_evolve(n_times_per_epoch=10000)
    # jlc._ik_solver.test_success_rate()
    base.run()
