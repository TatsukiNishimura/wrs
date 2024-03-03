import copy
import numpy as np
import robot_sim._kinematics.jl as rkjl
import robot_sim._kinematics.jlchain as rkjlc
import robot_sim._kinematics.model_generator as rkmg
import modeling.constant as mc
import basis.robot_math as rm


# ==============================================
# raise Exception if oiee is not empty
# ==============================================

def assert_oiee_decorator(method):
    def wrapper(self, *args, **kwargs):
        if len(self.oiee_list) > 0:
            raise ValueError("The hand is holding objects!")
        else:
            return method(self, *args, **kwargs)

    return wrapper


class EEInterface(object):

    def __init__(self, pos=np.zeros(3), rotmat=np.eye(3), cdmesh_type=mc.CDMType.AABB, name="end_effector"):
        self.name = name
        self.pos = pos
        self.rotmat = rotmat
        self.cdmesh_type = cdmesh_type  # aabb, convexhull, or triangles
        # joints
        # -- coupling --
        # no coupling by default, change the pos if the coupling existed
        # use loc flange create non-straight couplings
        self.coupling = rkjlc.JLChain(name=name + "_coupling", pos=self.pos, rotmat=self.rotmat)
        # acting center of the tool
        self.loc_acting_center_pos = np.zeros(3)
        self.loc_acting_center_rotmat = np.eye(3)
        # cd mesh collection for precise collision checking
        self.cdmesh_elements = []
        # object grasped/held/attached to end-effector; oiee = object in end-effector
        self.oiee_list = []

    def update_oiee(self):
        """
        :return:
        author: weiwei
        date: 20230807
        """
        for oiee in self.oiee_list:
            oiee.update_globals(pos=self.pos, rotmat=self.rotmat)

    @assert_oiee_decorator
    def hold(self, obj_cmodel, **kwargs):
        """
        the obj_cmodel is saved into an oiee_list, while considering its relative pose to the ee's pos and rotmat
        **kwargs is for polyphorism purpose
        :param obj_cmodel: a collision model
        :return:
        author: weiwei
        date: 20230811
        """
        obj_pos = obj_cmodel.pos
        obj_rotmat = obj_cmodel.rotmat
        rel_pos, rel_rotmat = rm.rel_pose(obj_pos, obj_rotmat, self.pos, self.rotmat)
        self.oiee_list.append(rkjl.Link(loc_pos=rel_pos, loc_rotmat=rel_rotmat, cmodel=obj_cmodel))

    def release(self, obj_cmodel, **kwargs):
        """
        the obj_cmodel is saved into an oiee_list, while considering its relative pose to the ee's pos and rotmat
        **kwargs is for polyphorism purpose
        :param obj_cmodel: a collision model
        :return:
        author: weiwei
        date: 20240228
        """
        is_found = False
        for oiee in self.oiee_list:
            if oiee.obj_cmodel is obj_cmodel:
                is_found = True
                self.oiee_list.remove(oiee)
                break
        if not is_found:
            raise ValueError("The specified object is not held in the hand!")

    def is_mesh_collided(self, cmodel_list=[], toggle_dbg=False):
        """
        check collision of cd meshes
        :param cmodel_list:
        :param toggle_dbg: show cd mesh and draw colliding points in case of collision
        :return:
        """
        for i, cdme in enumerate(self.cdmesh_elements):
            if cdme.obj_cmodel is not None:
                is_collided, collision_points = cdme.obj_cmodel.is_mcdwith(cmodel_list, True)
                if is_collided:
                    if toggle_dbg:
                        import modeling.geometric_model as mgm
                        cdme.obj_cmodel.show_cdmesh()
                        mgm.GeometricModel(cdme.obj_cmodel).attach_to(base)
                        for cmodel in cmodel_list:
                            cmodel.show_cdmesh()
                        print(collision_points)
                        for point in collision_points:
                            mgm.gen_sphere(point, radius=.001).attach_to(base)
                        print("mesh collided")
                    return True
        return False

    def fix_to(self, pos, rotmat):
        raise NotImplementedError

    @assert_oiee_decorator
    def align_acting_center_by_twovecs(self,
                                       acting_center_pos,
                                       approaching_vec,
                                       side_vec):
        """
        align acting center to a frame decided by two vectors
        :param acting_center_pos:
        :param approaching_vec:
        :param side_vec:
        :return:
        """
        acting_center_rotmat = np.eye(3)
        acting_center_rotmat[:, 2] = rm.unit_vector(approaching_vec)
        acting_center_rotmat[:, 1] = rm.unit_vector(side_vec)
        acting_center_rotmat[:, 0] = np.cross(acting_center_rotmat[:3, 1], acting_center_rotmat[:3, 2])
        return self.align_acting_center_by_pose(acting_center_pos=acting_center_pos,
                                                acting_center_rotmat=acting_center_rotmat)

    @assert_oiee_decorator
    def align_acting_center_by_pose(self, acting_center_pos, acting_center_rotmat):
        """
        align acting center to a frame decided by pos and rotmat
        :param acting_center_pos:
        :param acting_center_rotmat:
        :return:
        """
        ee_root_rotmat = acting_center_rotmat.dot(self.loc_acting_center_rotmat.T)
        ee_root_pos = acting_center_pos - ee_root_rotmat.dot(self.loc_acting_center_pos)
        self.fix_to(ee_root_pos, ee_root_rotmat)
        return [acting_center_pos, acting_center_rotmat, ee_root_pos, ee_root_rotmat]

    def gen_stickmodel(self, toggle_tcp_frame=False, toggle_jnt_frames=False, name='ee_stickmodel'):
        raise NotImplementedError

    def gen_meshmodel(self,
                      rgb=None,
                      alpha=None,
                      toggle_tcp_frame=False,
                      toggle_jnt_frames=False,
                      toggle_cdprim=False,
                      toggle_cdmesh=False,
                      name='ee_meshmodel'):
        raise NotImplementedError

    def gen_oiee_meshmodel(self,
                           m_col,
                           rgb=None,
                           alpha=None,
                           toggle_cdprim=False,
                           toggle_cdmesh=False,
                           toggle_frame=False):
        """
        :return:
        author: weiwei
        date: 20230807
        """
        for oiee in self.oiee_list:
            rkmg.gen_lnk_mesh(lnk=oiee, rgb=rgb, alpha=alpha, toggle_cdprim=toggle_cdprim, toggle_cdmesh=toggle_cdmesh,
                              toggle_frame=toggle_frame).attach_to(m_col)
            oiee.update_globals(pos=self.pos, rotmat=self.rotmat)

    def _toggle_tcp_frame(self, parent):
        gl_acting_center_pos = self.rotmat.dot(self.loc_acting_center_pos) + self.pos
        gl_acting_center_rotmat = self.rotmat.dot(self.loc_acting_center_rotmat)
        rkmg.gen_indicated_frame(spos=self.pos,
                                 gl_pos=gl_acting_center_pos,
                                 gl_rotmat=gl_acting_center_rotmat).attach_to(parent)