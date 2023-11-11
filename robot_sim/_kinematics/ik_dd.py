"""
Data driven ik solver
author: weiwei
date: 20231107
"""
import warnings

import numpy as np
import pickle
import basis.robot_math as rm
import scipy.spatial
from scipy.spatial.transform import Rotation
from tqdm import tqdm
import robot_sim._kinematics.ik_num as rkn
import robot_sim._kinematics.ik_opt as rko
import robot_sim._kinematics.ik_trac as rkt
# for debugging purpose
import modeling.geometric_model as mgm
import robot_sim._kinematics.model_generator as rkmg
import basis.constant as bc


class DDIKSolver(object):
    def __init__(self, jlc, path='./', backbone_solver='n', rebuild=False):
        """
        :param jlc:
        :param path:
        :param backbone_solver: 'n': num ik; 'o': opt ik; 't': trac ik
        :param rebuild:
        author: weiwei
        date: 20231111
        """
        self.jlc = jlc
        self.path = path
        self._k_val = 5  # number of nearest neighbours examined by the backbone sovler
        self._max_n_iter = 5  # max_n_iter of the backbone solver
        if backbone_solver == 'n':
            self._backbone_solver = rkn.NumIKSolver(self.jlc)
            self._backbone_solver_func = self._backbone_solver.pinv_wc
        elif backbone_solver == 'o':
            self._backbone_solver = rko.OptIKSolver(self.jlc)
            self._backbone_solver_func = self._backbone_solver.sqpss
        elif backbone_solver == 't':
            self._backbone_solver = rkt.TracIKSolver(self.jlc)
            self._backbone_solver_func = self._backbone_solver.ik
        if rebuild:
            y_or_n = input("Rebuilding the database needs new evolution and is cost. Do you want to continue?")
            if y_or_n == 'y':
                self.querry_tree, self.jnt_data = self._build_data()
                self._evolve_data()
                self._persist_data()
        else:
            try:
                self.querry_tree = pickle.load(open(self.path + 'ikdd_tree.pkl', 'rb'))
                self.jnt_data = pickle.load(open(self.path + 'jnt_data.pkl', 'rb'))
            except FileNotFoundError:
                self.querry_tree, self.jnt_data = self._build_data()
                self._evolve_data()
                self._persist_data()

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
                np.linspace(self.jlc.jnt_rngs[i][0], self.jlc.jnt_rngs[i][1], int(n_intervals[i]), endpoint=False))
        grid = np.meshgrid(*sampled_jnts)
        sampled_qs = np.vstack([x.ravel() for x in grid]).T
        # gen sampled qs and their correspondent tcps
        tcp_data = []
        jnt_data = []
        for id in tqdm(range(len(sampled_qs))):
            jnt_vals = sampled_qs[id]
            tcp_pos, tcp_rotmat = self.jlc.forward_kinematics(jnt_vals=jnt_vals, toggle_jac=False)
            tcp_rotvec = self._rotmat_to_vec(tcp_rotmat)
            tcp_data.append(np.concatenate((tcp_pos, tcp_rotvec)))
            jnt_data.append(jnt_vals)
        querry_tree = scipy.spatial.cKDTree(tcp_data)
        return querry_tree, jnt_data

    def _evolve_data(self, n_times=100000):
        for i in tqdm(range(n_times)):
            random_jnts = self.jlc.rand_conf()
            tgt_pos, tgt_rotmat = self.jlc.forward_kinematics(jnt_vals=random_jnts, update=False, toggle_jac=False)
            tcp_rotvec = self._rotmat_to_vec(tgt_rotmat)
            tgt_tcp = np.concatenate((tgt_pos, tcp_rotvec))
            dist_val_array, nn_indx_array = self.querry_tree.query(tgt_tcp, k=1000, workers=-1)
            is_solvable = False
            for nn_indx in nn_indx_array[:self._k_val]:
                seed_jnt_vals = self.jnt_data[nn_indx]
                result = self._backbone_solver_func(tgt_pos=tgt_pos,
                                                    tgt_rotmat=tgt_rotmat,
                                                    seed_jnt_vals=seed_jnt_vals,
                                                    max_n_iter=self._max_n_iter)
                if result is None:
                    continue
                else:
                    is_solvable = True
                    break
            if not is_solvable:
                # try solving the problem with additional nearest neighbours
                for id, nn_indx in enumerate(nn_indx_array[self._k_val:]):
                    seed_jnt_vals = self.jnt_data[nn_indx]
                    result = self._backbone_solver_func(tgt_pos=tgt_pos,
                                                        tgt_rotmat=tgt_rotmat,
                                                        seed_jnt_vals=seed_jnt_vals,
                                                        max_n_iter=self._max_n_iter)
                    if result is None:
                        continue
                    else:
                        # if solved, add the new jnts to the data and update the kd tree
                        tcp_data = np.vstack((self.querry_tree.data, tgt_tcp))
                        self.jnt_data.append(result)
                        self.querry_tree = scipy.spatial.cKDTree(tcp_data)
                        print(f"#### Previously unsolved ik solved using the {self._k_val+id}th nearest neighbour.")
                        break
        self.jlc._ik_solver.persist_evolution()
        print("Evolution is done.")

    def _test_success_rate(self, n_times=1000):
        success = 0
        time_list = []
        tgt_list = []
        for i in tqdm(range(1000), desc="ik"):
            random_jnts = self.jlc.rand_conf()
            tgt_pos, tgt_rotmat = self.jlc.forward_kinematics(jnt_vals=random_jnts, update=False, toggle_jac=False)
            tic = time.time()
            solved_jnt_vals = self.jlc.ik(tgt_pos=tgt_pos,
                                          tgt_rotmat=tgt_rotmat,
                                          # seed_jnt_vals=seed_jnt_vals,
                                          toggle_dbg=False)
            toc = time.time()
            time_list.append(toc - tic)
            if solved_jnt_vals is not None:
                success += 1
            else:
                tgt_list.append((tgt_pos, tgt_rotmat))
        print(f"The current success rate is: f{success / n_times}")
        print('average time cost', np.mean(time_list))
        print('max', np.max(time_list))
        print('min', np.min(time_list))
        print('std', np.std(time_list))
        return success / n_times

    def _persist_data(self):
        pickle.dump(self.querry_tree, open(self.path + 'ikdd_tree.pkl', 'wb'))
        pickle.dump(self.jnt_data, open(self.path + 'jnt_data.pkl', 'wb'))
        print("ddik data file saved.")

    def ik(self,
           tgt_pos,
           tgt_rotmat,
           seed_jnt_vals=None,
           toggle_dbg=False):
        """
        :param tgt_pos:
        :param tgt_rotmat:
        :param seed_jnt_vals: ignored
        :param toggle_dbg: ignored
        :return:
        author: weiwei
        date: 20231107
        """
        if seed_jnt_vals is not None:
            return self._backbone_solver_func(tgt_pos=tgt_pos,
                                              tgt_rotmat=tgt_rotmat,
                                              seed_jnt_vals=seed_jnt_vals,
                                              max_n_iter=self._max_n_iter)
        else:
            tcp_rotvec = self._rotmat_to_vec(tgt_rotmat)
            tgt_tcp = np.concatenate((tgt_pos, tcp_rotvec))
            dist_val_array, nn_indx_array = self.querry_tree.query(tgt_tcp, k=1000, workers=-1)
            for nn_indx in nn_indx_array[:5]:
                seed_jnt_vals = self.jnt_data[nn_indx]
                result = self._backbone_solver_func(tgt_pos=tgt_pos,
                                                    tgt_rotmat=tgt_rotmat,
                                                    seed_jnt_vals=seed_jnt_vals,
                                                    max_n_iter=self._max_n_iter)
                if result is None:
                    continue
                else:
                    return result
        return None


if __name__ == '__main__':
    import modeling.geometric_model as gm
    import robot_sim._kinematics.jlchain as rskj
    import time
    import basis.constant as bc
    import robot_sim._kinematics.model_generator as rkmg
    import visualization.panda.world as wd

    base = wd.World(cam_pos=[1.25, .75, .75], lookat_pos=[0, 0, .3])
    gm.gen_frame().attach_to(base)

    jlc = rskj.JLChain(n_dof=6)
    jlc.jnts[0].loc_pos = np.array([0, 0, 0])
    jlc.jnts[0].loc_motion_axis = np.array([0, 0, 1])
    jlc.jnts[0].motion_rng = np.array([-np.pi / 2, np.pi / 2])
    # jlc.joints[1].change_type(rkc.JointType.PRISMATIC)
    jlc.jnts[1].loc_pos = np.array([0, 0, .05])
    jlc.jnts[1].loc_motion_axis = np.array([0, 1, 0])
    jlc.jnts[1].motion_rng = np.array([-np.pi / 2, np.pi / 2])
    jlc.jnts[2].loc_pos = np.array([0, 0, .2])
    jlc.jnts[2].loc_motion_axis = np.array([0, 1, 0])
    jlc.jnts[2].motion_rng = np.array([-np.pi, np.pi])
    jlc.jnts[3].loc_pos = np.array([0, 0, .2])
    jlc.jnts[3].loc_motion_axis = np.array([0, 0, 1])
    jlc.jnts[3].motion_rng = np.array([-np.pi / 2, np.pi / 2])
    jlc.jnts[4].loc_pos = np.array([0, 0, .1])
    jlc.jnts[4].loc_motion_axis = np.array([0, 1, 0])
    jlc.jnts[4].motion_rng = np.array([-np.pi / 2, np.pi / 2])
    jlc.jnts[5].loc_pos = np.array([0, 0, .05])
    jlc.jnts[5].loc_motion_axis = np.array([0, 0, 1])
    jlc.jnts[5].motion_rng = np.array([-np.pi / 2, np.pi / 2])
    jlc.tcp_loc_pos = np.array([0, 0, .01])
    jlc.finalize()
    seed_jnt_vals = jlc.get_joint_values()

    # random_jnts = jlc.rand_conf()
    # tgt_pos, tgt_rotmat = jlc.forward_kinematics(jnt_vals=random_jnts, update=False, toggle_jac=False)
    # tic = time.time()
    # solved_jnt_vals = jlc.ik(tgt_pos=tgt_pos,
    #                   tgt_rotmat=tgt_rotmat,
    #                   max_n_iter=100)
    # gm.gen_frame(pos=tgt_pos, rotmat=tgt_rotmat).attach_to(base)
    # jlc.forward_kinematics(jnt_vals=solved_jnt_vals, update=True, toggle_jac=False)
    # rkmg.gen_jlc_stick(jlc, stick_rgba=bc.navy_blue, toggle_tcp_frame=True,
    #                    toggle_joint_frame=True).attach_to(base)
    # base.run()

    jlc._ik_solver._test_success_rate()
    base.run()
