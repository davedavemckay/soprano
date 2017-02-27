# Soprano - a library to crack crystals! by Simone Sturniolo
# Copyright (C) 2016 - Science and Technology Facility Council

# Soprano is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# Soprano is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Classes and functions for simulating approximated NMR spectroscopic results
from structures.
"""

# Python 2-to-3 compatibility code
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import re
import json
import pkgutil
import numpy as np
from ase import Atoms
from collections import namedtuple
from soprano.calculate.nmr.powder import gen_pwd_ang

try:
    _nmr_data = pkgutil.get_data('soprano',
                                 'data/nmrdata.json').decode('utf-8')
    _nmr_data = json.loads(_nmr_data)
except IOError:
    _nmr_data = None

# Conversion functions to Tesla
# (they take element and isotope as arguments)
_larm_units = {
    'MHz': lambda e, i: 2*np.pi*1.0e6/_nmr_data[e][i]['gamma'],
    'T': lambda e, i: 1.0,
}


def _el_iso(sym):
    """ Utility function: split isotope and element in conventional
    representation.
    """

    match = re.findall('([0-9]*)([A-Za-z]+)', sym)
    if len(match) != 1:
        raise ValueError('Invalid isotope symbol')
    elif match[0][1] not in _nmr_data:
        raise ValueError('Invalid element symbol')

    el = match[0][1]
    # What about the isotope?
    iso = str(_nmr_data[el]['iso']) if match[0][0] == '' else match[0][0]

    if iso not in _nmr_data[el]:
        raise ValueError('No data on isotope {0} for element {1}'.format(iso,
                                                                         el))

    return el, iso

# Flags for what to include in spectra
NMRFlags = namedtuple('NMRFlags',
                      """CS_ISO
                      CS_ORIENT
                      CS
                      Q_1_ORIENT
                      Q_2_SHIFT
                      Q_2_ORIENT
                      Q_2
                      Q
                      ALL"""
                      )
NMRFlags = NMRFlags(1, 2, 3, 4, 8, 16, 24, 28, 31)


class NMRCalculator(object):

    """NMRCalculator

    An object providing an interface to produce basic simulated NMR spectra
    from .magres files. It should be kept in mind that this is *not* a proper
    spin simulation tool, but merely provides a 'guide for the eye' kind of
    spectrum to compare to experimental results. What it can simulate:

    - chemical shift of NMR peaks
    - quadrupolar shifts of NMR peaks up to second order corrections
    - effects of crystal orientation (single crystal)
    - powder average (policrystalline/powder)

    What it can NOT simulate:

    - MAS effects
    - J couplings
    - complex multi-spin interactions
    - complex NMR experiments

    | Args:
    |   sample (ase.Atoms): an Atoms object describing the system to simulate
    |                       on. Should be loaded with ASE from a .magres file
    |                       if data on shieldings and EFGs is necessary. It
    |                       can also have an optional 'isotopes' array. If it
    |                       does, it will be used in the set_isotopes method
    |                       and interpreted as described in its documentation.
    |   larmor_frequency (float): larmor frequency of the virtual spectrometer
    |                             (referenced to Hydrogen). Default is 400.
    |   larmor_units (str): units in which the larmor frequency is expressed.
    |                       Default are MHz.

    """

    def __init__(self, sample, larmor_frequency=400,
                 larmor_units='MHz'):

        if not isinstance(sample, Atoms):
            raise TypeError('sample must be an ase.Atoms object')

        self._sample = sample

        # Define isotope array
        self._elems = np.array(self._sample.get_chemical_symbols())
        if self._sample.has('isotopes'):
            isos = self._sample.get_array('isotopes')
        else:
            isos = [None]*len(self._sample)
        self.set_isotopes(isos)

        self.set_larmor_frequency(larmor_frequency, larmor_units)

        self._references = {}

    def set_larmor_frequency(self, larmor_frequency=400, larmor_units='MHz',
                             element='1H'):
        """
        Set the Larmor frequency of the virtual spectrometer with the desired
        units and reference element.

        | Args:
        |   larmor_frequency (float): larmor frequency of the virtual
        |                             spectrometer. Default is 400.
        |   larmor_units (str): units in which the larmor frequency is
        |                       expressed. Can be MHz or T. Default are MHz.
        |   element (str): element and isotope to reference the frequency to.
        |                  Should be in the form <isotope><element>. Isotope
        |                  is optional, if absent the most abundant NMR active
        |                  one will be used. Default is 1H.

        """

        if larmor_units not in _larm_units:
            raise ValueError('Invalid units for Larmor frequency')

        # Split isotope and element
        el, iso = _el_iso(element)

        self._B = larmor_frequency*_larm_units[larmor_units](el, iso)

    def set_reference(self, ref, element):
        """
        Set the chemical shift reference (in ppm) for a given element. If not
        provided it will be assumed to be zero.

        | Args:
        |   ref (float): reference shielding value in ppm. Chemical shift will
        |                be calculated as this minus the atom's ms.
        |   element (str): element and isotope whose reference is set.
        |                  Should be in the form <isotope><element>. Isotope
        |                  is optional, if absent the most abundant NMR active
        |                  one will be used.

        """

        el, iso = _el_iso(element)

        if el not in self._references:
            self._references[el] = {}
        self._references[el][iso] = float(ref)

    def set_isotopes(self, isotopes):
        """
        Set the isotopes for each atom in sample.

        | Args:
        |   isotopes (list): list of isotopes for each atom in sample.
        |                    Isotopes can be given as an array of integers or
        |                    of symbols in the form <isotope><element>.
        |                    Their order must match the one of the atoms in
        |                    the original sample ase.Atoms object.
        |                    If an element of the list is None, the most
        |                    common NMR-active isotope is used. If an element
        |                    is the string 'Q', the most common quadrupolar
        |                    active isotope for that nucleus (if known) will
        |                    be used.

        """

        # First: correct length?
        if len(isotopes) != len(self._sample):
            raise ValueError('isotopes array should be as long as the atoms'
                             ' in sample')

        # Clean up the list, make sure it's all right
        iso_clean = []
        for i, iso in enumerate(isotopes):
            # Is it an integer?
            iso_name = ''
            if re.match('[0-9]+', str(iso)) is not None:    # numpy-proof test
                iso_name = str(iso)
                # Does it exist?
                if iso_name not in _nmr_data[self._elems[i]]:
                    raise ValueError('Invalid isotope '
                                     '{0} for element {1}'.format(iso_name,
                                                                  self.
                                                                  _elems[i]))
            elif iso is None:
                iso_name = str(_nmr_data[self._elems[i]]['iso'])
            elif iso == 'Q':
                iso_name = str(_nmr_data[self._elems[i]]['Q_iso'])
            else:
                el, iso_name = _el_iso(iso)
                # Additional test
                if el != self._elems[i]:
                    raise ValueError('Invalid element in isotope array - '
                                     '{0} in place of {1}'.format(el,
                                                                  self
                                                                  ._elems[i]))

            iso_clean.append(iso_name)

        self._isos = np.array(iso_clean)

    def set_element_isotope(self, element, isotope):
        """
        Set the isotope for all occurrences of a given element.

        | Args:
        |   element (str): chemical symbol of the element for which to set the
        |                  isotope.
        |   isotope (int or str): isotope to set for the given element. The
        |                         same conventions as described for the array
        |                         passed to set_isotopes apply.

        """

        new_isos = [int(x) for x in self._isos]
        new_isos = np.where(self._elems == element, isotope, new_isos)

        self.set_isotopes(new_isos)

    def set_single_crystal(self, theta, phi):
        """
        Set the orientation of the sample as a single crystallite.

        | Args:
        |   theta (float): zenithal angle for the crystallite
        |   phi (float): azimuthal angle for the crystallite

        """

        p = [np.sin(theta)*np.cos(phi),
             np.sin(theta)*np.sin(phi),
             np.cos(theta)]
        w = [1.0]
        t = []

        self._orients = [p, w, t]

    def set_powder(self, N=8, mode='hemisphere'):
        """
        Set the orientation of the sample as a powder average.

        | Args:
        |   N (int): the number of subdivisions used to generate orientations
        |            within the POWDER algorithm. Higher values make for
        |            better but more expensive averages.
        |   mode (str): which part of the solid angle to cover with the 
        |               orientations. Can be 'octant', 'hemisphere' or
        |               'sphere'. The latter should not be necessary for any
        |               NMR interaction. Default is 'hemisphere'.

        """

        self._orients = gen_pwd_ang(N, mode)

    def spectrum_1d(self, element, min_freq=-50, max_freq=50, bins=100,
                    freq_broad=None, freq_units='ppm',
                    effects=NMRFlags.MS_SHIFT):
        """
        Return a simulated spectrum for the given sample and element.

        <to be expanded>
        """

        # First, define the frequency range
        e, i = _el_iso(element)
        larm = self._B*_nmr_data[e][i]['gamma']/(2.0*np.pi*1e6)
        # Units? We want this to be in ppm
        u = {
            'ppm': 1,
            'MHz': 1e6/larm,
        }
        try:
            freq_axis = np.linspace(min_freq, max_freq, bins)*u[freq_units]
        except KeyError:
            raise ValueError('Invalid freq_units passed to spectrum_1d')

        # Ok, so get the relevant atoms and their properties
        a_inds = np.where((self._elems == e) & (self._isos == i))[0]

        if effects & NMRFlags.CS != 0:
            try:
                ms_tens = self._sample.get_array('ms')
            except KeyError:
                raise RuntimeError('Impossible to compute chemical shift - '
                                   'sample has no shielding data')

        if effects & NMRFlags.Q != 0:
            try:
                efg_tens = self._sample.get_array('efg')
            except KeyError:
                raise RuntimeError('Impossible to compute quadrupolar effects'
                                   ' - sample has no EFG data')
