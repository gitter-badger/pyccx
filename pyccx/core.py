# -*- coding: utf-8 -*-


import re  # used to get info from frd file
import os
import sys
import subprocess  # used to check ccx version
from enum import Enum, auto
from typing import List
import logging

from .mesh import Mesher

import gmsh
import numpy as np


class AnalysisError(Exception):
    """Exception raised for errors generated during the analysis

    Attributes:
        expression -- input expression in which the error occurred
        message -- explanation of the error
    """

    def __init__(self, expression, message):
        self.expression = expression
        self.message = message


class AnalysisType(Enum):
    STRUCTURAL = auto()
    THERMAL = auto()
    FLUID = auto()

class Simulation:
    """
    Provides the base class for running a Calculix simulation
    """

    NUMTHREADS = 1
    CALCULIX_PATH = ''

    def __init__(self, meshModel: Mesher):

        self._input = ''

        # List of materials
        self.materials = []
        self.materialAssignments = []
        self.model = meshModel

        self.initialTimeStep = 0.1
        self.defaultTimeStep = 0.1
        self.totalTime = 1.0
        self.useSteadyStateAnalysis = True

        self.TZERO = -273.15
        self.SIGMAB = 5.669E-8
        self._numThreads = 1

        self.initialConditions = []  # 'dict of node set names,
        self.loadCases = []

        self._nodeSets = []
        self._elSets = []

        self.nodeSets = []
        self.elSets = []
        self.includes = []

    def init(self):

        self._input = ''
        self._nodeSets = self.nodeSets
        self._elSets = self.elSets

    @classmethod
    def setNumThreads(cls, numThreads: int):
        """
        Sets the number of simulation threads to use in Calculix

        :param numThreads:
        :return:
        """
        cls.NUMTHREADS = numThreads

    @classmethod
    def getNumThreads(cls) -> int:
        """
        Returns the number of threads used

        :return: int:
        """
        return cls.NUMTHREADS

    @classmethod
    def setCalculixPath(cls, calculixPath: str):
        """
        Sets the path for the Calculix executable. Necessary when using Windows where there is not a default
        installation proceedure for Calculix

        :param calculixPath: str Directory containing the Calculix Executable
        """

        if os.path.isdir(calculixPath):
            cls.CALCULIX_PATH = calculixPath

    @property
    def name(self):
        return self._name

    def writeHeaders(self):

        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' INCLUDES ')

        for filename in self.includes:
            self._input += '*include,input={:s}'.format(filename)

    def prepareConnectors(self):
        """
        Creates node sets for any RBE connectors used in the simulation
        """
        # Kinematic Connectors require creating node sets
        # These are created and added to the node set collection prior to writing

        numConnectors = 1

        for connector in self.connectors:
            # Create an nodal set
            self.nodeSets.append({'name' : 'connector_{:s}'.format(connector['name']),
                                  'nodes': connector['nodes']})

            numConnectors += 1

    def writeInput(self) -> str:
        """
        Writes the input deck for the simulation
        """

        self.init()

        self.prepareConnectors()

        self.writeHeaders()
        self.writeMesh()
        self.writeNodeSets()
        self.writeElementSets()
        self.writeKinematicConnectors()
        self.writeMPCs()
        self.writeMaterials()
        self.writeMaterialAssignments()
        self.writeInitialConditions()
        self.writeAnalysisConditions()
        self.writeLoadSteps()

        return self._input

    def writeElementSets(self):

        if len(self._elSets) == 0:
            return

        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' ELEMENT SETS ')

        for elSet in self._elSets:
            self._input += os.linesep
            self._input += '*ELSET,ELSET={:s\n}'.format(elSet['name'])
            self._input += np.array2string(elSet['els'], precision=2, separator=', ', threshold=9999999999)[1:-1]

    def writeNodeSets(self):

        if len(self._nodeSets) == 0:
            return

        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' NODE SETS ')

        for nodeSet in self._nodeSets:
            self._input += os.linesep
            self._input += '*NSET,NSET={:s}\n'.format(nodeSet['name'])
            self._input += np.array2string(nodeSet['nodes'], precision=2, separator=', ', threshold=9999999999)[1:-1]

    def writeKinematicConnectors(self):

        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' KINEMATIC CONNECTORS ')

        for connector in self.connectors:

            # A nodeset is automatically created from the name of the connector
            self.input += '*RIGIDBODY, NSET={:s}'.format(connector['name'])

            # A reference node is optional
            if isinstance(connector['refnode'], int):
                self.input += ',REF NODE={:d}\n'.format(connector['refnode'])
            else:
                self.input += '\n'

    def writeMPCs(self):

        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' MPCS ')

        for mpcSet in self.mpcSets:
            self.input += '*EQUATION\n'
            self.input += '{:d}\n'.format(len(mpcSet['numTerms']))  # Assume each line constrains two nodes and one dof
            for mpc in mpcSet['equations']:
                for i in range(len(mpc['eqn'])):
                    self._input += '{:d},{:d},{:d}'.format(mpc['node'][i], mpc['dof'][i], mpc['eqn'][i])

                self.input += os.linesep

    #        *EQUATION
    #        2 # number of terms in equation # typically two
    #        28,2,1.,22,2,-1. # node a id, dof, node b id, dof b

    def writeMaterialAssignments(self):
        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' MATERIAL ASSIGNMENTS ')

        for matAssignment in self.materialAssignments:
            self._input += '*solid section, elset={:s}, material={:s}\n'.format(matAssignment[0], matAssignment[1])

    def writeMaterials(self):
        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' MATERIALS ')
        for material in self.materials:
            self._input += material.writeInput()

    def writeInitialConditions(self):
        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' INITIAL CONDITIONS ')

        for initCond in self.initialConditions:
            self._input += '*INITIAL CONDITIONS,TYPE={:s}\n'.format(initCond['type'].upper())
            self._input += '{:s},{:e}\n'.format(initCond['set'], initCond['value'])
            self._input += os.linesep

        # Write the Physical Constants
        self._input += '*PHYSICAL CONSTANTS,ABSOLUTE ZERO={:e},STEFAN BOLTZMANN={:e}\n'.format(self.TZERO, self.SIGMAB)

    def writeAnalysisConditions(self):

        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' ANALYSIS CONDITIONS ')

        # Write the Initial Timestep
        self._input += '{:.3f}, {:.3f}\n'.format(self.initialTimeStep, self.defaultTimeStep)

    def writeLoadSteps(self):

        self._input += os.linesep
        self._input += '{:*^125}\n'.format(' LOAD STEPS ')

        for loadCase in self.loadCases:
            self._input += loadCase.writeInput()

    def writeMesh(self):

        # TODO make a unique auto-generated name for the mesh
        meshFilename = 'mesh.inp'

        self.model.writeMesh(meshFilename)
        self._input += '*include,input={:s}'.format(meshFilename)

    def checkAnalysis(self) -> bool:
        """
        Routine checks that the analysis has been correctly generated

        :return: bool: True if no analysis error occur
        :raise: AnalysisError: Analysis error that occured
        """

        if len(self.materials) == 0:
            raise AnalysisError('No material models have been assigned to the analysis')

        for material in self.materials:
            if not material.isValid():
                raise AnalysisError('Material ({:s}) is not valid'.format(material.name))

        return True

    def run(self):

        self.checkAnalysis()

        print('============== Writing Input File ==================\n \n')
        inputDeckContents = self.writeInput()

        with open("input.inp", "w") as text_file:
            text_file.write(inputDeckContents)

        # Set environment variables for performing multi-threaded
        os.environ["CCX_NPROC_STIFFNESS"] = '{:d}'.format(Simulation.NUMTHREADS)
        os.environ["CCX_NPROC_EQUATION_SOLVER"] = '{:d}'.format(Simulation.NUMTHREADS)
        os.environ["OMP_NUM_THREADS"] = '{:d}'.format(Simulation.NUMTHREADS)

        if sys.platform == 'win32':
            cmdPath = os.path.join(self.CALCULIX_PATH, 'ccx.exe')
            arguments = '-i input'

            cmd = cmdPath + arguments

            # direct_output = subprocess.check_output('ccx.exe -i input', shell=True) #could be anything here.
            print('============== Running Calculix ================== \n')

            popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True)
            for stdout_line in iter(popen.stdout.readline, ""):
                print(stdout_line, end='')

            popen.stdout.close()
            return_code = popen.wait()
            if return_code:
                raise subprocess.CalledProcessError(return_code, cmd)
        else:
            raise NotImplemented(' Platform is not currently supported')