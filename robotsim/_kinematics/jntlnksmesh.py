import os
import numpy as np
import copy
from panda3d.core import NodePath
import modeling.geometricmodel as gm
import modeling.collisionmodel as cm
import basis.robotmath as rm


class JntLnksMesh(object):
    """
    The mesh generator class for JntLnks

    NOTE: it is unnecessary to attach a nodepath to render repeatedly
    once attached, it is always there. update the joint angles
    will change the attached model directly
    """

    def __init__(self, jlobject):
        """
        author: weiwei
        date: 20200331
        """
        self.jlobject = jlobject
        for id in range(self.jlobject.ndof + 1):
            if self.jlobject.links[id]['meshfile'] is not None:
                self.jlobject.links[id]['collisionmodel'] = cm.CollisionModel(self.jlobject.links[id]['meshfile'])

    def gen_meshmodel(self, tcp_jntid=None, tcp_localpos=None, tcp_localrotmat=None, toggletcpcs=True,
                      togglejntscs=False, name='robotmesh', drawhand=True, rgbargt=None, rgbalft=None):
        meshmodel = gm.StaticGeometricModel(name=name)
        for id in range(self.jlobject.ndof + 1):
            if self.jlobject.links[id]['collisionmodel'] is not None:
                this_collisionmodel = self.jlobject.links[id]['collisionmodel'].copy()
                pos = self.jlobject.links[id]['g_pos']
                rotmat = self.jlobject.links[id]['g_rotmat']
                this_collisionmodel.sethomomat(rm.homomat_from_posrot(pos, rotmat))
                this_collisionmodel.setcolor(self.jlobject.links[id]['rgba'])
                this_collisionmodel.reparent_to(meshmodel)
        # tool center coord
        if toggletcpcs:
            self._toggle_tcpcs(meshmodel, tcp_jntid, tcp_localpos, tcp_localrotmat, tcpic_rgba=np.array([.5, 0, 1, 0]), tcpic_thickness=.0062)
        # toggle all coord
        if togglejntscs:
            self._toggle_jntcs(meshmodel, jntcs_thickness=.0062)
        return meshmodel

    def gen_stickmodel(self, rgba=np.array([.5, 0, 0, 1]), thickness=.01, jointratio=1.62, linkratio=.62,
                       tcp_jntid=None, tcp_localpos=None, tcp_localrotmat=None,
                       toggletcpcs=True, togglejntscs=False, togglecntjnt=False, name='robotstick'):
        """
        generate the stick model for a jntlnk object
        snp means stick nodepath

        :param jlobject:
        :param rgba:
        :param tcp_jntid:
        :param tcp_localpos:
        :param tcp_localrotmat:
        :param toggletcpcs:
        :param togglejntscs:
        :param togglecntjnt: draw the connecting joint explicitly or not
        :param name:
        :return:

        author: weiwei
        date: 20200331, 20201006
        """
        stickmodel = gm.StaticGeometricModel(name=name)
        id = 0
        loopdof = self.jlobject.ndof + 1
        if togglecntjnt:
            loopdof = self.jlobject.ndof + 2
        while id < loopdof:
            cjid = self.jlobject.joints[id]['child']
            jgpos = self.jlobject.joints[id]['g_posq']  # joint global pos
            cjgpos = self.jlobject.joints[cjid]['g_pos0']  # child joint global pos
            jgmtnax = self.jlobject.joints[id]["g_mtnax"]  # joint global rot ax
            gm.genstick(spos=jgpos, epos=cjgpos, thickness=thickness, type="rect", rgba=rgba).reparent_to(stickmodel)
            if id > 0:
                if self.jlobject.joints[id]['type'] == "revolute":
                    gm.genstick(spos=jgpos - jgmtnax * thickness, epos=jgpos + jgmtnax * thickness, type="rect",
                                thickness=thickness * jointratio, rgba=np.array([.3, .3, .2, 1])).reparent_to(
                        stickmodel)
                if self.jlobject.joints[id]['type'] == "prismatic":
                    jgpos0 = self.jlobject.joints[id]['g_pos0']
                    gm.genstick(spos=jgpos0, epos=jgpos, type="round",
                                thickness=thickness * jointratio, rgba=np.array([.2, .3, .3, 1])).reparent_to(
                        stickmodel)
            id = cjid
        # tool center coord
        if toggletcpcs:
            self._toggle_tcpcs(stickmodel, tcp_jntid, tcp_localpos, tcp_localrotmat,
                               tcpic_rgba=rgba + np.array([0, 0, 1, 0]), tcpic_thickness=thickness * linkratio)
        # toggle all coord
        if togglejntscs:
            self._toggle_jntcs(stickmodel, jntcs_thickness=thickness * linkratio)
        return stickmodel

    def gen_endsphere(self, rgba=None, name=''):
        """
        generate an end sphere (es) to show the trajectory of the end effector

        :param jlobject: a JntLnk object
        :param rbga: color of the arm
        :return: null

        author: weiwei
        date: 20181003madrid, 20200331
        """

        eesphere = gm.StaticGeometricModel(name=name)
        if rgba is not None:
            gm.gensphere(pos=self.jlobject.joints[-1]['linkend'], radius=.025, rgba=rgba).reparent_to(eesphere)
        return gm.StaticGeometricModel(eesphere)

    def _toggle_tcpcs(self, parentmodel, tcp_jntid, tcp_localpos, tcp_localrotmat, tcpic_rgba, tcpic_thickness,
                      tcpcs_thickness=None, tcpcs_length=None):
        """
        :param parentmodel: where to draw the frames to
        :param tcp_jntid: single id or a list of ids
        :param tcp_localpos:
        :param tcp_localrotmat:
        :param tcpic_rgba: color that used to render the tcp indicator
        :param tcpic_thickness: thickness the tcp indicator
        :param tcpcs_thickness: thickness the tcp coordinate frame
        :return:

        author: weiwei
        date: 20201125
        """
        if tcp_jntid is None:
            tcp_jntid = self.jlobject.tcp_jntid
        if tcp_localpos is None:
            tcp_localpos = self.jlobject.tcp_localpos
        if tcp_localrotmat is None:
            tcp_localrotmat = self.jlobject.tcp_localrotmat
        if tcpcs_thickness is None:
            tcpcs_thickness = tcpic_thickness
        if tcpcs_length is None:
            tcpcs_length = tcpcs_thickness * 15
        tcp_globalpos, tcp_globalrotmat = self.jlobject.get_globaltcp(tcp_jntid, tcp_localpos, tcp_localrotmat)
        if isinstance(tcp_globalpos, list):
            for i, jid in enumerate(tcp_jntid):
                jgpos = self.jlobject.joints[jid]['g_posq']
                gm.gendumbbell(spos=jgpos, epos=tcp_globalpos[i], thickness=tcpic_thickness,
                               rgba=tcpic_rgba).reparent_to(parentmodel)
                gm.genframe(pos=tcp_globalpos[i], rotmat=tcp_globalrotmat[i], length=tcpcs_length,
                            thickness=tcpcs_thickness, alpha=1).reparent_to(parentmodel)
        else:
            jgpos = self.jlobject.joints[tcp_jntid]['g_posq']
            gm.gendumbbell(spos=jgpos, epos=tcp_globalpos, thickness=tcpic_thickness, rgba=tcpic_rgba).reparent_to(
                parentmodel)
            gm.genframe(pos=tcp_globalpos, rotmat=tcp_globalrotmat, length=tcpcs_length, thickness=tcpcs_thickness,
                        alpha=1).reparent_to(parentmodel)

    def _toggle_jntcs(self, parentmodel, jntcs_thickness, jntcs_length=None):
        """
        :param parentmodel: where to draw the frames to
        :return:

        author: weiwei
        date: 20201125
        """
        if jntcs_length is None:
            jntcs_length = jntcs_thickness * 15
        for id in self.jlobject.tgtjnts:
            gm.gendashframe(pos=self.jlobject.joints[id]['g_pos0'], rotmat=self.jlobject.joints[id]['g_rotmat0'],
                            length=jntcs_length, thickness=jntcs_thickness).reparent_to(parentmodel)
            gm.genframe(pos=self.jlobject.joints[id]['g_posq'], rotmat=self.jlobject.joints[id]['g_rotmatq'],
                        length=jntcs_length, thickness=jntcs_thickness, alpha=1).reparent_to(parentmodel)
