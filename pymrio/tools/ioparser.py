"""
Various parser for available MRIOs and files in a similar format
as

KST 20140903
"""

import os
import re
import logging
import warnings

import pandas as pd
import numpy as np
import scipy.io
import scipy.sparse as sp
import zipfile
from collections import namedtuple

from pymrio.core.mriosystem import IOSystem
from pymrio.core.mriosystem import Extension
from pymrio.core.fileio import load_all
from pymrio.tools.iometadata import MRIOMetaData
from pymrio.tools.ioutil import sniff_csv_format
from pymrio.tools.ioutil import get_repo_content

# Constants and global variables
from pymrio.core.constants import PYMRIO_PATH

from pymrio.tools.iomath import div0


# Exceptions
class ParserError(Exception):
    """ Base class for errors concerning parsing of IO source files """
    pass


class ParserWarning(UserWarning):
    """ Base class for warnings concerning parsing of IO source files """
    pass


IDX_NAMES = {
    'Z_col': ['region', 'sector'],
    'Z_row': ['region', 'sector'],
    'Z_row_unit': ['region', 'sector', 'unit'],
    'A_col': ['region', 'sector'],
    'A_row': ['region', 'sector'],
    'A_row_unit': ['region', 'sector', 'unit'],
    'Y_col1': ['region'],
    'Y_col2': ['region', 'category'],
    'Y_row': ['region', 'sector'],
    'Y_row_unit': ['region', 'sector', 'unit'],
    'F_col': ['region', 'sector'],
    'F_row_single': ['stressor'],
    'F_row_unit': ['stressor', 'unit'],
    'F_row_comp_unit': ['stressor', 'compartment', 'unit'],
    'F_row_src_unit': ['stressor', 'source', 'unit'],
    'F_row_src': ['stressor', 'source'],
    'VA_row_single': ['inputtype'],
    'VA_row_unit': ['inputtype', 'unit'],
    'VA_row_unit_cat': ['inputtype', 'category'],
    'unit': ['unit'],
    '_reg_sec_unit': ['region', 'sector', 'unit'],
}


# Top level functions
def parse_exio12_ext(ext_file, index_col, name, drop_compartment=True,
                     version=None, year=None, iosystem=None, sep=','):
    """ Parse an EXIOBASE version 1 or 2 like extension file into pymrio.Extension

    EXIOBASE like extensions files are assumed to have two
    rows which are used as columns multiindex (region and sector)
    and up to three columns for the row index (see Parameters).

    For EXIOBASE 3 - extension can be loaded directly with pymrio.load

    Notes
    -----
    So far this only parses factor of production extensions F (not
    final demand extensions F_Y nor coeffiecents S).

    Parameters
    ----------

    ext_file : string or pathlib.Path
        File to parse

    index_col : int
        The number of columns (1 to 3) at the beginning of the file
        to use as the index. The order of the index_col must be
        - 1 index column: ['stressor']
        - 2 index columns: ['stressor', 'unit']
        - 3 index columns: ['stressor', 'compartment', 'unit']
        - > 3: everything up to three index columns will be removed

    name : string
        Name of the extension

    drop_compartment : boolean, optional
        If True (default) removes the compartment from the index.

    version : string, optional
        see pymrio.Extension

    iosystem : string, optional
        see pymrio.Extension

    year : string or int
        see pymrio.Extension

    sep : string, optional
        Delimiter to use; default ','

    Returns
    -------
    pymrio.Extension
        with F (and unit if available)

    """

    ext_file = os.path.abspath(str(ext_file))

    F = pd.read_csv(
        ext_file,
        header=[0, 1],
        index_col=list(range(index_col)),
        sep=sep)

    F.columns.names = ['region', 'sector']

    if index_col == 1:
        F.index.names = ['stressor']

    elif index_col == 2:
        F.index.names = ['stressor', 'unit']

    elif index_col == 3:
        F.index.names = ['stressor', 'compartment', 'unit']

    else:
        F.reset_index(level=list(range(3, index_col)),
                      drop=True,
                      inplace=True)
        F.index.names = ['stressor', 'compartment', 'unit']

    unit = None
    if index_col > 1:
        unit = pd.DataFrame(F.iloc[:, 0].
                            reset_index(level='unit').unit)
        F.reset_index(level='unit', drop=True, inplace=True)

    if drop_compartment:
        try:
            F.reset_index(level='compartment',
                          drop=True, inplace=True)
            unit.reset_index(level='compartment',
                             drop=True, inplace=True)
        except KeyError:
            # In case compartment was not part to begin with
            pass

    return Extension(name=name,
                     F=F,
                     unit=unit,
                     iosystem=iosystem,
                     version=version,
                     year=year,
                     )


def get_exiobase12_version(filename):
    """ Returns the EXIOBASE version for the given filename,
        None if not found
    """
    try:
        ver_match = re.search(r'(\d+\w*(\.|\-|\_))*\d+\w*', filename)
        version = ver_match.string[ver_match.start():ver_match.end()]
        if re.search(r'\_\d\d\d\d', version[-5:]):
            version = version[:-5]
    except AttributeError:
        version = None

    return version


def get_exiobase_files(path, coefficients=True):
    """ Gets the EXIOBASE files in path (which can be a zip file)

    Parameters
    ----------
    path: str or pathlib.Path
        Path to exiobase files or zip file
    coefficients: boolean, optional
        If True (default), considers the mrIot file as A matrix,
        and the extensions as S matrices. Otherwise as Z and F, respectively

    Returns
    -------
    dict of dict
    """
    path = os.path.normpath(str(path))
    if coefficients:
        exio_core_regex = dict(
            # don’t match file if starting with _
            A=re.compile(r'(?<!\_)mrIot.*txt'),
            Y=re.compile(r'(?<!\_)mrFinalDemand.*txt'),
            S_factor_inputs=re.compile(r'(?<!\_)mrFactorInputs.*txt'),
            S_emissions=re.compile(r'(?<!\_)mrEmissions.*txt'),
            S_materials=re.compile(r'(?<!\_)mrMaterials.*txt'),
            S_resources=re.compile(r'(?<!\_)mrResources.*txt'),
            F_Y_resources=re.compile(r'(?<!\_)mrFDResources.*txt'),
            F_Y_emissions=re.compile(r'(?<!\_)mrFDEmissions.*txt'),
            F_Y_materials=re.compile(r'(?<!\_)mrFDMaterials.*txt'),
        )
    else:
        exio_core_regex = dict(
            # don’t match file if starting with _
            Z=re.compile(r'(?<!\_)mrIot.*txt'),
            Y=re.compile(r'(?<!\_)mrFinalDemand.*txt'),
            F_factor_inputs=re.compile(r'(?<!\_)mrFactorInputs.*txt'),
            F_emissions=re.compile(r'(?<!\_)mrEmissions.*txt'),
            F_materials=re.compile(r'(?<!\_)mrMaterials.*txt'),
            F_resources=re.compile(r'(?<!\_)mrResources.*txt'),
            F_Y_emissions=re.compile(r'(?<!\_)mrFDEmissions.*txt'),
            F_Y_materials=re.compile(r'(?<!\_)mrFDMaterials.*txt'),
        )

    repo_content = get_repo_content(path)

    exio_files = dict()
    for kk, vv in exio_core_regex.items():
        found_file = [vv.search(ff).string for ff in repo_content.filelist
                      if vv.search(ff)]
        if len(found_file) > 1:
            logging.warning(
                "Multiple files found for {}: {}"
                " - USING THE FIRST ONE".format(kk, found_file))
            found_file = found_file[0:1]
        elif len(found_file) == 0:
            continue
        else:
            logging.debug(f'Process file {found_file[0]}')
            if repo_content.iszip:
                format_para = sniff_csv_format(found_file[0],
                                               zip_file=path)
            else:
                format_para = sniff_csv_format(os.path.join(path,
                                                            found_file[0]))
            exio_files[kk] = dict(
                root_repo=path,
                file_path=found_file[0],
                version=get_exiobase12_version(
                    os.path.basename(found_file[0])),
                index_rows=format_para['nr_header_row'],
                index_col=format_para['nr_index_col'],
                unit_col=format_para['nr_index_col'] - 1,
                sep=format_para['sep'])

    return exio_files


def generic_exiobase12_parser(exio_files, system=None):
    """ Generic EXIOBASE version 1 and 2 parser

    This is used internally by parse_exiobase1 / 2 functions to
    parse exiobase files. In most cases, these top-level functions
    should just work, but in case of archived exiobase versions
    it might be necessary to use low-level function here.

    Parameters
    ----------

    exio_files: dict of dict

    system: str (pxp or ixi)
        Only used for the metadata

    """

    version = ' & '.join({dd.get('version', '')
                          for dd in exio_files.values()
                          if dd.get('version', '')})

    meta_rec = MRIOMetaData(system=system,
                            name="EXIOBASE",
                            version=version)

    if len(version) == 0:
        meta_rec.note("No version information found, assuming exiobase 1")
        meta_rec.change_meta('version', 1)
        version = '1'

    core_components = ['A', 'Y', 'Z']

    core_data = dict()
    ext_data = dict()
    for tt, tpara in exio_files.items():
        full_file_path = os.path.join(tpara['root_repo'], tpara['file_path'])
        logging.debug("Parse {}".format(full_file_path))
        if tpara['root_repo'][-3:] == 'zip':
            with zipfile.ZipFile(tpara['root_repo'], 'r') as zz:
                raw_data = pd.read_csv(
                    zz.open(tpara['file_path']),
                    index_col=list(range(tpara['index_col'])),
                    header=list(range(tpara['index_rows'])),
                    sep='\t')
        else:
            raw_data = pd.read_csv(
                full_file_path,
                index_col=list(range(tpara['index_col'])),
                header=list(range(tpara['index_rows'])),
                sep='\t')

        meta_rec._add_fileio('EXIOBASE data {} parsed from {}'.format(
            tt, full_file_path))
        if tt in core_components:
            core_data[tt] = raw_data
        else:
            ext_data[tt] = raw_data

    for table in core_data:
        core_data[table].index.names = ['region', 'sector', 'unit']
        if table == 'A' or table == 'Z':
            core_data[table].columns.names = ['region', 'sector']
            _unit = pd.DataFrame(
                core_data[table].iloc[:, 0]).reset_index(
                level='unit').unit
            _unit = pd.DataFrame(_unit)
            _unit.columns = ['unit']
        if table == 'Y':
            core_data[table].columns.names = ['region', 'category']
        core_data[table].reset_index(level='unit', drop=True, inplace=True)

    core_data['unit'] = _unit

    mon_unit = core_data['unit'].iloc[0, 0]
    if '/' in mon_unit:
        mon_unit = mon_unit.split('/')[0]
        core_data['unit'].unit = mon_unit

    extensions = dict()
    for tt, tpara in exio_files.items():
        if tt in core_components:
            continue

        # The following depends on the format (upper/lower case) of the
        # dict keys returned by get_exiobase_files
        ext_name = '_'.join(re.findall(r'[a-z]+', tt))
        table_type = re.match(r'[A-Z_]+', tt)[0].rstrip('_')

        if tpara['index_col'] == 3:
            ext_data[tt].index.names = [
                'stressor', 'compartment', 'unit']
        elif tpara['index_col'] == 2:
            ext_data[tt].index.names = [
                'stressor', 'unit']
        else:
            raise ParserError('Unknown EXIOBASE file structure')

        if table_type == 'F_Y':
            ext_data[tt].columns.names = [
                'region', 'category']
        else:
            ext_data[tt].columns.names = [
                'region', 'sector']
        try:
            _unit = pd.DataFrame(
                ext_data[tt].iloc[:, 0]
            ).reset_index(level='unit').unit
        except IndexError:
            _unit = pd.DataFrame(
                ext_data[tt].iloc[:, 0])
            _unit.columns = ['unit']
            _unit['unit'] = 'undef'
            _unit.reset_index(level='unit', drop=True, inplace=True)
            _unit = pd.DataFrame(_unit)
            _unit.columns = ['unit']

        _unit = pd.DataFrame(_unit)
        _unit.columns = ['unit']
        _new_unit = _unit.unit.str.replace('/'+mon_unit, '')
        _new_unit[_new_unit == ''] = _unit.unit[
            _new_unit == ''].str.replace('/', '')
        _unit.unit = _new_unit

        ext_data[tt].reset_index(level='unit', drop=True, inplace=True)
        ext_dict = extensions.get(ext_name, dict())
        ext_dict.update({table_type: ext_data[tt],
                         'unit': _unit,
                         'name': ext_name})
        extensions.update({ext_name: ext_dict})

    if version[0] == '1':
        year = 2000
    elif version[0] == '2':
        year = 2000
    elif version[0] == '3':
        raise ParserError(
            "This function can not be used to parse EXIOBASE 3")
    else:
        logging.warning("Unknown EXIOBASE version")
        year = None

    return IOSystem(version=version,
                    price='current',
                    year=year,
                    meta=meta_rec,
                    **dict(core_data, **extensions))


def _get_MRIO_system(path):
    """ Extract system information (ixi, pxp) from file path.

    Returns 'ixi' or 'pxp', None in undetermined
    """
    ispxp = True if re.search('pxp', path, flags=re.IGNORECASE) else False
    isixi = True if re.search('ixi', path, flags=re.IGNORECASE) else False

    if ispxp == isixi:
        system = None
    else:
        system = 'pxp' if ispxp else 'ixi'
    return system


def parse_exiobase1(path):
    """ Parse the exiobase1 raw data files.

    This function works with

    - pxp_ita_44_regions_coeff_txt
    - ixi_fpa_44_regions_coeff_txt
    - pxp_ita_44_regions_coeff_src_txt
    - ixi_fpa_44_regions_coeff_src_txt

    which can be found on www.exiobase.eu

    The parser works with the compressed (zip) files as well as the unpacked
    files.

    Parameters
    ----------
    path : pathlib.Path or string
        Path of the exiobase 1 data

    Returns
    -------
    pymrio.IOSystem with exio1 data

    """
    path = os.path.abspath(os.path.normpath(str(path)))

    exio_files = get_exiobase_files(path)
    if len(exio_files) == 0:
        raise ParserError("No EXIOBASE files found at {}".format(path))

    system = _get_MRIO_system(path)
    if not system:
        logging.warning("Could not determine system (pxp or ixi)"
                        " set system parameter manually")

    io = generic_exiobase12_parser(exio_files, system=system)
    return io


def parse_exiobase2(path, charact=True, popvector='exio2'):
    """ Parse the exiobase 2.2.2 source files for the IOSystem

    The function parse product by product and industry by industry source file
    in the coefficient form (A and S).

    Filenames are hardcoded in the parser - for any other function the code has
    to be adopted. Check git comments to find older verions.

    Parameters
    ----------
    path : string or pathlib.Path
        Path to the EXIOBASE source files
    charact : string or boolean, optional
        Filename with path to the characterisation matrices for the extensions
        (xls). This is provided together with the EXIOBASE system and given as
        a xls file. The four sheets  Q_factorinputs, Q_emission, Q_materials
        and Q_resources are read and used to generate one new extensions with
        the impacts.
        If set to True, the characterisation file found in path is used (
        can be in the zip or extracted). If a string, it is assumed that
        it points to valid characterisation file. If False or None, no
        characterisation file will be used.
    popvector : string or pd.DataFrame, optional
        The population vector for the countries.  This can be given as
        pd.DataFrame(index = population, columns = countrynames) or, (default)
        will be taken from the pymrio module. If popvector = None no population
        data will be passed to the IOSystem.

    Returns
    -------
    IOSystem
        A IOSystem with the parsed exiobase 2 data

    Raises
    ------
    ParserError
        If the exiobase source files are not complete in the given path

    """
    path = os.path.abspath(os.path.normpath(str(path)))

    exio_files = get_exiobase_files(path)
    if len(exio_files) == 0:
        raise ParserError("No EXIOBASE files found at {}".format(path))

    system = _get_MRIO_system(path)
    if not system:
        logging.warning("Could not determine system (pxp or ixi)"
                        " set system parameter manually")

    io = generic_exiobase12_parser(exio_files, system=system)

    # read the characterisation matrices if available
    # and build one extension with the impacts
    if charact:
        logging.debug('Parse characterisation matrix')
        # dict with correspondence to the extensions
        Qsheets = {'Q_factorinputs': 'factor_inputs',
                   'Q_emission': 'emissions',
                   'Q_materials': 'materials',
                   'Q_resources': 'resources'}

        Q_head_col = dict()
        Q_head_row = dict()
        Q_head_col_rowname = dict()
        Q_head_col_rowunit = dict()
        # Q_head_col_metadata = dict()
        # number of cols containing row headers at the beginning
        Q_head_col['Q_emission'] = 4
        # number of rows containing col headers at the top - this will be
        # skipped
        Q_head_row['Q_emission'] = 3
        # assuming the same classification as in the extensions
        Q_head_col['Q_factorinputs'] = 2
        Q_head_row['Q_factorinputs'] = 2
        Q_head_col['Q_resources'] = 2
        Q_head_row['Q_resources'] = 3
        Q_head_col['Q_materials'] = 2
        Q_head_row['Q_materials'] = 2

        #  column to use as name for the rows
        Q_head_col_rowname['Q_emission'] = 1
        Q_head_col_rowname['Q_factorinputs'] = 0
        Q_head_col_rowname['Q_resources'] = 0
        Q_head_col_rowname['Q_materials'] = 0

        # column to use as unit for the rows which gives also the last column
        # before the data
        Q_head_col_rowunit['Q_emission'] = 3
        Q_head_col_rowunit['Q_factorinputs'] = 1
        Q_head_col_rowunit['Q_resources'] = 1
        Q_head_col_rowunit['Q_materials'] = 1

        if charact is str:
            charac_data = {Qname: pd.read_excel(
                           charact,
                           sheet_name=Qname,
                           skiprows=list(range(0, Q_head_row[Qname])),
                           header=None)
                           for Qname in Qsheets}
        else:
            _content = get_repo_content(path)
            charac_regex = re.compile(r'(?<!\_)(?<!\.)characterisation.*xlsx')
            charac_files = [ff for ff in _content.filelist if
                            re.search(charac_regex, ff)]
            if len(charac_files) > 1:
                raise ParserError(
                    "Found multiple characcterisation files "
                    "in {} - specify one: {}".format(path, charac_files))
            elif len(charac_files) == 0:
                raise ParserError(
                    "No characcterisation file found "
                    "in {}".format(path))
            else:
                if _content.iszip:
                    with zipfile.ZipFile(path, 'r') as zz:
                        charac_data = {Qname: pd.read_excel(
                                       zz.open(charac_files[0]),
                                       sheet_name=Qname,
                                       skiprows=list(
                                           range(0, Q_head_row[Qname])),
                                       header=None)
                                       for Qname in Qsheets}

                else:
                    charac_data = {Qname: pd.read_excel(
                                   os.path.join(path, charac_files[0]),
                                   sheet_name=Qname,
                                   skiprows=list(range(0, Q_head_row[Qname])),
                                   header=None)
                                   for Qname in Qsheets}

        _unit = dict()
        # temp for the calculated impacts which than
        # get summarized in the 'impact'
        _impact = dict()
        impact = dict()
        for Qname in Qsheets:
            # unfortunately the names in Q_emissions are
            # not completely unique - fix that
            if Qname is 'Q_emission':
                _index = charac_data[Qname][Q_head_col_rowname[Qname]].copy()
                _index.iloc[42] = _index.iloc[42] + ' 2008'
                _index.iloc[43] = _index.iloc[43] + ' 2008'
                _index.iloc[44] = _index.iloc[44] + ' 2010'
                _index.iloc[45] = _index.iloc[45] + ' 2010'
                charac_data[Qname][Q_head_col_rowname[Qname]] = _index

            charac_data[Qname].index = (
                charac_data[Qname][Q_head_col_rowname[Qname]])

            _unit[Qname] = pd.DataFrame(
                charac_data[Qname].iloc[:, Q_head_col_rowunit[Qname]])
            _unit[Qname].columns = ['unit']
            _unit[Qname].index.name = 'impact'
            charac_data[Qname] = charac_data[Qname].iloc[
                :, Q_head_col_rowunit[Qname]+1:]
            charac_data[Qname].index.name = 'impact'

            try:
                _F_Y = io.__dict__[Qsheets[Qname]].F_Y.values
            except AttributeError:
                _F_Y = np.zeros([io.__dict__[Qsheets[Qname]].S.shape[0],
                                 io.Y.shape[1]])

            _impact[Qname] = {'S': charac_data[Qname].dot(
                io.__dict__[Qsheets[Qname]].S.values),
                'F_Y': charac_data[Qname].dot(_F_Y),
                'unit': _unit[Qname]
            }

        impact['S'] = (_impact['Q_factorinputs']['S']
                       .append(_impact['Q_emission']['S'])
                       .append(_impact['Q_materials']['S'])
                       .append(_impact['Q_resources']['S']))
        impact['F_Y'] = (_impact['Q_factorinputs']['F_Y']
                         .append(_impact['Q_emission']['F_Y'])
                         .append(_impact['Q_materials']['F_Y'])
                         .append(_impact['Q_resources']['F_Y']))
        impact['S'].columns = io.emissions.S.columns
        impact['F_Y'].columns = io.emissions.F_Y.columns
        impact['unit'] = (_impact['Q_factorinputs']['unit']
                          .append(_impact['Q_emission']['unit'])
                          .append(_impact['Q_materials']['unit'])
                          .append(_impact['Q_resources']['unit']))
        impact['name'] = 'impact'
        io.impact = Extension(**impact)

    if popvector is 'exio2':
        logging.debug('Read population vector')
        io.population = pd.read_csv(os.path.join(PYMRIO_PATH['exio20'],
                                                 './misc/population.txt'),
                                    index_col=0, sep='\t').astype(float)
    else:
        io.population = popvector

    return io


def parse_exiobase3(path):
    """ Parses the public EXIOBASE 3 system

    This parser works with either the compressed zip
    archive as downloaded or the extracted system.

    Note
    ----
    The exiobase 3 parser does so far not include
    population and characterization data.

    Parameters
    ----------

    path : string or pathlib.Path
        Path to the folder with the EXIOBASE files
        or the compressed archive.

    Returns
    -------
    IOSystem
        A IOSystem with the parsed exiobase 3 data

    """
    io = load_all(path)
    # need to rename the final demand satellite,
    # wrong name in the standard distribution
    try:
        io.satellite.F_Y = io.satellite.F_hh.copy()
        del io.satellite.F_hh
    except AttributeError:
        pass

    # some ixi in the exiobase 3.4 official distribution
    # have a country name mixup. Clean it here:
    io.rename_regions(
        {'AUS': 'AU',
         'AUT': 'AT',
         'BEL': 'BE',
         'BGR': 'BG',
         'BRA': 'BR',
         'CAN': 'CA',
         'CHE': 'CH',
         'CHN': 'CN',
         'CYP': 'CY',
         'CZE': 'CZ',
         'DEU': 'DE',
         'DNK': 'DK',
         'ESP': 'ES',
         'EST': 'EE',
         'FIN': 'FI',
         'FRA': 'FR',
         'GBR': 'GB',
         'GRC': 'GR',
         'HRV': 'HR',
         'HUN': 'HU',
         'IDN': 'ID',
         'IND': 'IN',
         'IRL': 'IE',
         'ITA': 'IT',
         'JPN': 'JP',
         'KOR': 'KR',
         'LTU': 'LT',
         'LUX': 'LU',
         'LVA': 'LV',
         'MEX': 'MX',
         'MLT': 'MT',
         'NLD': 'NL',
         'NOR': 'NO',
         'POL': 'PL',
         'PRT': 'PT',
         'ROM': 'RO',
         'RUS': 'RU',
         'SVK': 'SK',
         'SVN': 'SI',
         'SWE': 'SE',
         'TUR': 'TR',
         'TWN': 'TW',
         'USA': 'US',
         'ZAF': 'ZA',
         'WWA': 'WA',
         'WWE': 'WE',
         'WWF': 'WF',
         'WWL': 'WL',
         'WWM': 'WM'})

    return io


def parse_wiod(path, year=None, names=('isic', 'c_codes'),
               popvector=None):
    """ Parse the wiod source files for the IOSystem

    WIOD provides the MRIO tables in excel - format (xlsx) at
    http://www.wiod.org/new_site/database/wiots.htm (release November 2013).
    To use WIOD in pymrio these (for the year of analysis) must be downloaded.
    The interindustry matrix of these files gets parsed in IOSystem.Z, the
    additional information is included as factor_input extension (value
    added,...)

    The folder with these xslx must than be passed to the WIOD parsing
    function. This folder may contain folders with the extension data. Every
    folder within the wiod root folder will be parsed for extension data and
    will be added to the IOSystem. The WIOD database offers the download of
    the environmental extensions as zip files. These can be read directly by
    the parser. In case a zip file and a folder with the same name are
    available, the data is read from the folder. If the zip files are
    extracted into folder, the folders must have the same name as the
    corresponding zip file (without the 'zip' extension).

    If a WIOD SEA file is present (at the root of path or in a folder named
    'SEA' - only one file!), the labor data of this file gets included in the
    factor_input extension (calculated for the the three skill levels
    available). The monetary data in this file is not added because it is only
    given in national currency.

    Since the "World Input-Output Tables in previous years' prices" are still
    under construction (20141129), no parser for these is provided.

    Some of the meta-parameter of the IOSystem are set automatically based on
    the values given in the first four cells and the name of the WIOD data
    files (base year, version, price, iosystem).
    These can be overwritten afterwards if needed.

    Parameters
    ----------
    path : string or pathlib.Path
        Path to the folder with the WIOD source files. In case that the path
        to a specific file is given, only this will be parsed irrespective of
        the values given in year.
    year : int or str
        Which year in the path should be parsed. The years can be given with
        four or two digits (eg [2012 or 12]). If the given path contains a
        specific file, the value of year will not be used (but inferred from
        the meta data)- otherwise it must be given For the monetary data the
        parser searches for files with 'wiot - two digit year'.
    names : string or tuple, optional
        WIOD provides three different sector/final demand categories naming
        schemes. These can can be specified for the IOSystem. Pass:

            1) 'isic': ISIC rev 3 Codes - available for interindustry flows
               and final demand rows.
            2) 'full': Full names - available for final demand rows and
               final demand columns (categories) and interindustry flows.
            3) 'c_codes' : WIOD specific sector numbers, available for final
               demand rows and columns (categories) and interindustry flows.

        Internally, the parser relies on 1) for the interindustry flows and 3)
        for the final demand categories. This is the default and will also be
        used if just 'isic' gets passed ('c_codes' also replace 'isic' if this
        was passed for final demand categories). To specify different finial
        consumption category names, pass a tuple with (sectors/interindustry
        classification, fd categories), eg ('isic', 'full'). Names are case
        insensitive and passing the first character is sufficient.
    TODO popvector : TO BE IMPLEMENTED (consistent with EXIOBASE)

    Returns
    -------
    IOSystem

    Raises
    ------
    ParserError
        If the WIOD source file are not complete or inconsistent

    """

    # Path manipulation, should work cross platform
    path = os.path.abspath(os.path.normpath(str(path)))

    # wiot start and end
    wiot_ext = '.xlsx'
    wiot_start = 'wiot'

    # determine which wiod file to be parsed
    if not os.path.isdir(path):
        # 1. case - one file specified in path
        if os.path.isfile(path):
            wiot_file = path
        else:
            # just in case the ending was forgotten
            wiot_file = path + wiot_ext
    else:
        # 2. case: directory given-build wiot_file with the value given in year
        if not year:
            raise ParserError('No year specified '
                              '(either specify a specific file '
                              'or a path and year)')
        year_two_digit = str(year)[-2:]
        wiot_file_list = [fl for fl in os.listdir(path)
                          if (fl[:6] == wiot_start + year_two_digit and
                              os.path.splitext(fl)[1] == wiot_ext)]
        if len(wiot_file_list) != 1:
            raise ParserError('Multiple files for a given year or file not '
                              'found (specify a specific file in paramters)')

        wiot_file = os.path.join(path, wiot_file_list[0])

    wiot_file = wiot_file
    root_path = os.path.split(wiot_file)[0]
    if not os.path.exists(wiot_file):
        raise ParserError('WIOD file not found in the specified folder.')

    meta_rec = MRIOMetaData(location=root_path)

    # wiot file structure
    wiot_meta = {
        'col': 0,   # column of the meta information
        'year': 0,  # rest: rows with the data
        'iosystem': 2,
        'unit': 3,
        'end_row': 4,
    }
    wiot_header = {
        # the header indexes are the same for rows after removing the first
        # two lines (wiot_empty_top_rows)
        'code': 0,
        'sector_names': 1,
        'region': 2,
        'c_code': 3,
    }
    wiot_empty_top_rows = [0, 1]

    wiot_marks = {   # special marks
        'last_interindsec': 'c35',     # last sector of the interindustry
        'tot_facinp': ['r60', 'r69'],  # useless totals to remove from factinp
        'total_column': [-1],          # the total column in the whole data
    }

    wiot_sheet = 0   # assume the first one is the one with the data.

    # Wiod has an unfortunate file structure with overlapping metadata and
    # header. In order to deal with that first the full file is read.
    wiot_data = pd.read_excel(wiot_file,
                              sheet_name=wiot_sheet,
                              header=None)

    meta_rec._add_fileio('WIOD data parsed from {}'.format(wiot_file))
    # get meta data
    wiot_year = wiot_data.iloc[wiot_meta['year'], wiot_meta['col']][-4:]
    wiot_iosystem = wiot_data.iloc[
        wiot_meta['iosystem'], wiot_meta['col']].rstrip(')').lstrip('(')
    meta_rec.change_meta('system', wiot_iosystem)
    _wiot_unit = wiot_data.iloc[
        wiot_meta['unit'], wiot_meta['col']].rstrip(')').lstrip('(')

    # remove meta data, empty rows, total column
    wiot_data.iloc[0:wiot_meta['end_row'], wiot_meta['col']] = np.NaN
    wiot_data.drop(wiot_empty_top_rows,
                   axis=0, inplace=True)
    wiot_data.drop(wiot_data.columns[wiot_marks['total_column']],
                   axis=1, inplace=True)
    # at this stage row and column header should have the same size but
    # the index starts now at two - replace/reset to row numbers
    wiot_data.index = range(wiot_data.shape[0])

    # Early years in WIOD tables have a different name for Romania:
    # 'ROM' which should be 'ROU'. The latter is also consistent with
    # the environmental extensions names.
    wiot_data.iloc[wiot_header['region'], :] = wiot_data.iloc[
        wiot_header['region'], :].str.replace('ROM', 'ROU')
    wiot_data.iloc[:, wiot_header['region']] = wiot_data.iloc[
        :, wiot_header['region']].str.replace('ROM', 'ROU')

    # get the end of the interindustry matrix
    _lastZcol = wiot_data[
        wiot_data.iloc[
            :, wiot_header['c_code']] == wiot_marks['last_interindsec']
    ].index[-1]
    _lastZrow = wiot_data[
        wiot_data[wiot_header['c_code']] == wiot_marks['last_interindsec']
    ].index[-1]

    if _lastZcol != _lastZrow:
        raise ParserError(
            'Interindustry matrix not symetric in the WIOD source file')
    else:
        Zshape = (_lastZrow, _lastZcol)

    # separate factor input extension and remove
    # totals in the first and last row
    facinp = wiot_data.iloc[Zshape[0]+1:, :]
    facinp = facinp.drop(
        facinp[facinp[wiot_header['c_code']].isin(
            wiot_marks['tot_facinp'])].index, axis=0
    )

    Z = wiot_data.iloc[:Zshape[0]+1, :Zshape[1]+1].copy()
    Y = wiot_data.iloc[:Zshape[0]+1, Zshape[1]+1:].copy()
    F_fac = facinp.iloc[:, :Zshape[1]+1].copy()
    F_Y_fac = facinp.iloc[:, Zshape[1]+1:].copy()

    index_wiot_headers = [nr for nr in wiot_header.values()]
    # Save lookup of sectors and codes - to be used at the end of the parser
    # Assuming USA is present in every WIOT year
    wiot_sector_lookup = wiot_data[
        wiot_data[wiot_header['region']] == 'USA'].iloc[
            :, 0:max(index_wiot_headers)+1].applymap(str)
    wiot_sector_lookup.columns = [
        entry[1] for entry in sorted(
            zip(wiot_header.values(), wiot_header.keys()))]
    wiot_sector_lookup.set_index('code', inplace=True, drop=False)
    _Y = Y.T.iloc[:, [
        wiot_header['code'],  # Included to be consistent with  wiot_header
        wiot_header['sector_names'],
        wiot_header['region'],
        wiot_header['c_code'],
    ]]
    wiot_fd_lookup = _Y[_Y.iloc[
        :, wiot_header['region']] == 'USA'].applymap(str)
    wiot_fd_lookup.columns = [
        entry[1] for entry in
        sorted(zip(wiot_header.values(), wiot_header.keys()))]
    wiot_fd_lookup.set_index('c_code', inplace=True, drop=False)
    wiot_fd_lookup.index.name = 'code'

    # set the index/columns, work with code b/c these are also used in the
    # extensions
    Z[wiot_header['code']] = Z[wiot_header['code']].astype(str)
    Z.set_index([wiot_header['region'],
                 wiot_header['code']], inplace=True, drop=False)
    Z = Z.iloc[max(index_wiot_headers)+1:, max(index_wiot_headers)+1:]
    Z.index.names = IDX_NAMES['Z_col']
    Z.columns = Z.index

    indexY_col_head = Y.iloc[[wiot_header['region'],
                              wiot_header['c_code']], :]
    Y.columns = pd.MultiIndex.from_arrays(indexY_col_head.values,
                                          names=IDX_NAMES['Y_col2'])
    Y = Y.iloc[max(index_wiot_headers)+1:, :]
    Y.index = Z.index

    F_fac.set_index([wiot_header['sector_names']],
                    inplace=True, drop=False)  # c_code missing, use names
    F_fac.index.names = ['inputtype']
    F_fac = F_fac.iloc[:, max(index_wiot_headers)+1:]
    F_fac.columns = Z.columns
    F_Y_fac.columns = Y.columns
    F_Y_fac.index = F_fac.index

    # convert from object to float (was object because mixed float,str)
    Z = Z.astype('float')
    Y = Y.astype('float')
    F_fac = F_fac.astype('float')
    F_Y_fac = F_Y_fac.astype('float')

    # save the units
    Z_unit = pd.DataFrame(Z.iloc[:, 0])
    Z_unit.columns = ['unit']
    Z_unit['unit'] = _wiot_unit

    F_fac_unit = pd.DataFrame(F_fac.iloc[:, 0])
    F_fac_unit.columns = ['unit']
    F_fac_unit['unit'] = _wiot_unit

    ll_countries = list(Z.index.get_level_values('region').unique())

    # Finalize the factor inputs extension
    ext = dict()

    ext['factor_inputs'] = {'F': F_fac,
                            'F_Y': F_Y_fac,
                            'year': wiot_year,
                            'iosystem': wiot_iosystem,
                            'unit': F_fac_unit,
                            'name': 'factor input',
                            }

    # SEA extension
    _F_sea_data, _F_sea_unit = __get_WIOD_SEA_extension(
        root_path=root_path, year=wiot_year)
    if _F_sea_data is not None:
        # None if no SEA file present
        _F_Y_sea = pd.DataFrame(index=_F_sea_data.index,
                                columns=F_Y_fac.columns, data=0)
        _F_Y_sea = _F_Y_sea.astype('float')

        ext['SEA'] = {'F': _F_sea_data,
                      'F_Y': _F_Y_sea,
                      'year': wiot_year,
                      'iosystem': wiot_iosystem,
                      'unit': _F_sea_unit,
                      'name': 'SEA',
                      }
        meta_rec._add_fileio('SEA file extension parsed from {}'.format(
            root_path))

    # Environmental extensions, names follow the name given
    # in the meta sheet (except for CO2 to get a better description).
    # Units are hardcoded if no consistent place to read them
    # within the files (for all extensions in upper case).
    # The units names must exactly match!
    # Start must identify exactly one folder or a zip file to
    # read the extension.
    # Within the folder, the routine looks for xls files
    # starting with the country code.
    dl_envext_para = {
        'AIR': {'name': 'Air Emission Accounts',
                'start': 'AIR_',
                'ext': '.xls',
                'unit': {
                        'CO2': 'Gg',
                        'CH4': 't',
                        'N2O': 't',
                    'NOx': 't',
                    'SOx': 't',
                    'CO': 't',
                    'NMVOC': 't',
                    'NH3': 't',
                },
                },
        'CO2': {'name': 'CO2 emissions - per source',
                'start': 'CO2_',
                'ext': '.xls',
                'unit': {
                        'all': 'Gg'}
                },

        'EM': {'name': 'Emission relevant energy use',
               'start': 'EM_',
               'ext': '.xls',
               'unit': {
                       'all': 'TJ'}
               },
        'EU': {'name': 'Gross energy use',
               'start': 'EU_',
               'ext': '.xls',
               'unit': {
                       'all': 'TJ'}
               },
        'lan': {'name': 'land use',
                'start': 'lan_',
                'ext': '.xls',
                'unit': {
                        'all': None}
                },
        'mat': {'name': 'material use',
                'start': 'mat_',
                'ext': '.xls',
                'unit': {
                        'all': None}
                },
        'wat': {'name': 'water use',
                'start': 'wat_',
                'ext': '.xls',
                'unit': {
                        'all': None}
                },
    }

    _F_Y_template = pd.DataFrame(columns=F_Y_fac.columns)
    _ss_F_Y_pressure_column = 'c37'
    for ik_ext in dl_envext_para:
        _dl_ex = __get_WIOD_env_extension(root_path=root_path,
                                          year=wiot_year,
                                          ll_co=ll_countries,
                                          para=dl_envext_para[ik_ext])
        if _dl_ex is not None:
            # None if extension not available
            _F_Y = _dl_ex['F_Y']

            _F_Y.columns = pd.MultiIndex.from_product([
                _F_Y.columns, [_ss_F_Y_pressure_column]])
            _F_Y = _F_Y_template.append(_F_Y)
            _F_Y.fillna(0, inplace=True)
            _F_Y.index.names = _dl_ex['F'].index.names
            _F_Y.columns.names = _F_Y_template.columns.names
            _F_Y = _F_Y[ll_countries]
            _F_Y = _F_Y.astype('float')

            ext[ik_ext] = {
                'F': _dl_ex['F'],
                'F_Y': _F_Y,
                'year': wiot_year,
                'iosystem': wiot_iosystem,
                'unit': _dl_ex['unit'],
                'name': dl_envext_para[ik_ext]['name'],
            }
            meta_rec._add_fileio('Extension {} parsed from {}'.format(
                ik_ext, root_path))

    # Build system
    wiod = IOSystem(Z=Z, Y=Y,
                    unit=Z_unit,
                    meta=meta_rec,
                    **ext)

    # Replace sector/final demand category names
    if type(names) is str:
        names = (names, names)
    ll_names = [w[0].lower() for w in names]

    if ll_names[0] == 'c':
        dd_sec_rename = wiot_sector_lookup.c_code.to_dict()
    elif ll_names[0] == 'i':
        dd_sec_rename = wiot_sector_lookup.code.to_dict()
    elif ll_names[0] == 'f':
        dd_sec_rename = wiot_sector_lookup.sector_names.to_dict()
    else:
        dd_sec_rename = wiot_sector_lookup.code.to_dict()
        warnings.warn('Parameter for names not understood - '
                      'used ISIC codes as sector names')

    if ll_names[1] == 'c':
        dd_fd_rename = wiot_fd_lookup.c_code.to_dict()
    elif ll_names[1] == 'i':
        dd_fd_rename = wiot_fd_lookup.c_code.to_dict()
    elif ll_names[1] == 'f':
        dd_fd_rename = wiot_fd_lookup.sector_names.to_dict()
    else:
        warnings.warn('Parameter for names not understood - '
                      'used c_codes as final demand category names')

    wiod.Z.rename(columns=dd_sec_rename, index=dd_sec_rename, inplace=True)
    wiod.Y.rename(columns=dd_fd_rename, index=dd_sec_rename, inplace=True)
    for ext in wiod.get_extensions(data=True):
        ext.F.rename(columns=dd_sec_rename, inplace=True)
        ext.F_Y.rename(columns=dd_fd_rename, inplace=True)

    return wiod


def __get_WIOD_env_extension(root_path, year, ll_co, para):
    """ Parses the wiod environmental extension

    Extension can either be given as original .zip files or as extracted
    data in a folder with the same name as the corresponding zip file (with-
    out the extension).

    This function is based on the structure of the extensions from _may12.

    Note
    ----
    The function deletes 'secQ' which is not present in the economic tables.

    Parameters
    ----------
    root_path : string
        Path to the WIOD data or the path with the
        extension data folder or zip file.
    year : str or int
        Year to return for the extension = valid sheetname for the xls file.
    ll_co : list like
        List of countries in WIOD - used for finding and matching
        extension data in the given folder.
    para : dict
        Defining the parameters for reading the extension.

    Returns
    -------
    dict with keys
        F : pd.DataFrame with index 'stressor' and columns 'region', 'sector'
        F_Y : pd.Dataframe with index 'stressor' and column 'region'
            This data is for household stressors - must be applied to the right
            final demand column afterwards.
        unit : pd.DataFrame with index 'stressor' and column 'unit'


    """

    ll_root_content = [ff for ff in os.listdir(root_path) if
                       ff.startswith(para['start'])]
    if len(ll_root_content) < 1:
        warnings.warn(
            'Extension data for {} not found - '
            'Extension not included'.format(para['start']), ParserWarning)
        return None

    elif len(ll_root_content) > 1:
        raise ParserError(
            'Several raw data for extension'
            '{} available - clean extension folder.'.format(para['start']))

    pf_env = os.path.join(root_path, ll_root_content[0])

    if pf_env.endswith('.zip'):
        rf_zip = zipfile.ZipFile(pf_env)
        ll_env_content = [ff for ff in rf_zip.namelist() if
                          ff.endswith(para['ext'])]
    else:
        ll_env_content = [ff for ff in os.listdir(pf_env) if
                          ff.endswith(para['ext'])]

    dl_env = dict()
    dl_env_hh = dict()
    for co in ll_co:
        ll_pff_read = [ff for ff in ll_env_content if
                       ff.endswith(para['ext']) and
                       (ff.startswith(co.upper()) or
                        ff.startswith(co.lower()))]

        if len(ll_pff_read) < 1:
            raise ParserError('Country data not complete for Extension '
                              '{} - missing {}.'.format(para['start'], co))

        elif len(ll_pff_read) > 1:
            raise ParserError('Multiple country data for Extension '
                              '{} - country {}.'.format(para['start'], co))

        pff_read = ll_pff_read[0]

        if pf_env.endswith('.zip'):
            ff_excel = pd.ExcelFile(rf_zip.open(pff_read))
        else:
            ff_excel = pd.ExcelFile(os.path.join(pf_env, pff_read))
        if str(year) in ff_excel.sheet_names:
            df_env = ff_excel.parse(sheet_name=str(year),
                                    index_col=None,
                                    header=0
                                    )
        else:
            warnings.warn('Extension {} does not include'
                          'data for the year {} - '
                          'Extension not included'.format(para['start'], year),
                          ParserWarning)
            return None

        if not df_env.index.is_numeric():
            # upper case letter extensions gets parsed with multiindex, not
            # quite sure why...
            df_env.reset_index(inplace=True)

        # unit can be taken from the first cell in the excel sheet
        if df_env.columns[0] != 'level_0':
            para['unit']['all'] = df_env.columns[0]

        # two clean up cases - can be identified by lower/upper case extension
        # description
        if para['start'].islower():
            pass
        elif para['start'].isupper():
            df_env = df_env.iloc[:, 1:]
        else:
            raise ParserError('Format of extension not given.')

        df_env.dropna(axis=0, how='all', inplace=True)
        df_env = df_env[df_env.iloc[:, 0] != 'total']
        df_env = df_env[df_env.iloc[:, 0] != 'secTOT']
        df_env = df_env[df_env.iloc[:, 0] != 'secQ']
        df_env.iloc[:, 0] = df_env.iloc[:, 0].astype(str)
        df_env.iloc[:, 0].replace(to_replace='sec',
                                  value='',
                                  regex=True,
                                  inplace=True)

        df_env.set_index([df_env.columns[0]], inplace=True)
        df_env.index.names = ['sector']
        df_env = df_env.T

        ikc_hh = 'FC_HH'
        dl_env_hh[co] = df_env[ikc_hh]
        del df_env[ikc_hh]
        dl_env[co] = df_env

    df_F = pd.concat(dl_env, axis=1)[ll_co]
    df_F_Y = pd.concat(dl_env_hh, axis=1)[ll_co]
    df_F.fillna(0, inplace=True)
    df_F_Y.fillna(0, inplace=True)

    df_F.columns.names = IDX_NAMES['F_col']
    df_F.index.names = IDX_NAMES['F_row_single']

    df_F_Y.columns.names = IDX_NAMES['Y_col1']
    df_F_Y.index.names = IDX_NAMES['F_row_single']

    # build the unit df
    df_unit = pd.DataFrame(index=df_F.index, columns=['unit'])
    _ss_unit = para['unit'].get('all', 'undef')
    for ikr in df_unit.index:
        df_unit.loc[ikr, 'unit'] = para['unit'].get(ikr, _ss_unit)

    df_unit.columns.names = ['unit']
    df_unit.index.names = ['stressor']

    if pf_env.endswith('.zip'):
        rf_zip.close()

    return {'F': df_F,
            'F_Y': df_F_Y,
            'unit': df_unit
            }


def __get_WIOD_SEA_extension(root_path, year, data_sheet='DATA'):
    """ Utility function to get the extension data from the SEA file in WIOD

    This function is based on the structure in the WIOD_SEA_July14 file.
    Missing values are set to zero.

    The function works if the SEA file is either in path or in a subfolder
    named 'SEA'.

    Parameters
    ----------
    root_path : string
        Path to the WIOD data or the path with the SEA data.
    year : str or int
        Year to return for the extension
    sea_data_sheet : string, optional
        Worksheet with the SEA data in the excel file

    Returns
    -------
    SEA data as extension for the WIOD MRIO
    """
    sea_ext = '.xlsx'
    sea_start = 'WIOD_SEA'

    _SEA_folder = os.path.join(root_path, 'SEA')
    if not os.path.exists(_SEA_folder):
        _SEA_folder = root_path

    sea_folder_content = [ff for ff in os.listdir(_SEA_folder)
                          if os.path.splitext(ff)[-1] == sea_ext and
                          ff[:8] == sea_start]

    if sea_folder_content:
        # read data
        sea_file = os.path.join(_SEA_folder, sorted(sea_folder_content)[0])

        df_sea = pd.read_excel(sea_file,
                               sheet_name=data_sheet,
                               header=0,
                               index_col=[0, 1, 2, 3])

        # fix years
        ic_sea = df_sea.columns.tolist()
        ic_sea = [yystr.lstrip('_') for yystr in ic_sea]
        df_sea.columns = ic_sea

        try:
            ds_sea = df_sea[str(year)]
        except KeyError:
            warnings.warn(
                'SEA extension does not include data for the '
                'year {} - SEA-Extension not included'.format(year),
                ParserWarning)
            return None, None

        # get useful data (employment)
        mt_sea = ['EMP', 'EMPE', 'H_EMP', 'H_EMPE']
        ds_use_sea = pd.concat(
            [ds_sea.xs(key=vari, level='Variable', drop_level=False)
             for vari in mt_sea])
        ds_use_sea.drop(labels='TOT', level='Code', inplace=True)
        ds_use_sea.reset_index('Description', drop=True, inplace=True)

        # RoW not included in SEA but needed to get it consistent for
        # all countries. Just add a dummy with 0 for all accounts.
        if 'RoW' not in ds_use_sea.index.get_level_values('Country'):
            ds_RoW = ds_use_sea.xs('USA',
                                   level='Country', drop_level=False)
            ds_RoW.loc[:] = 0
            df_RoW = ds_RoW.reset_index()
            df_RoW['Country'] = 'RoW'
            ds_use_sea = pd.concat(
                [ds_use_sea.reset_index(), df_RoW]).set_index(
                ['Country', 'Code', 'Variable'])

        ds_use_sea.fillna(value=0, inplace=True)
        df_use_sea = ds_use_sea.unstack(level=['Country', 'Code'])[str(year)]
        df_use_sea.index.names = IDX_NAMES['VA_row_single']
        df_use_sea.columns.names = IDX_NAMES['F_col']
        df_use_sea = df_use_sea.astype('float')

        df_unit = pd.DataFrame(
            data=[    # this data must be in the same order as mt_sea
                'thousand persons',
                'thousand persons',
                'mill hours',
                'mill hours',
            ],
            columns=['unit'],
            index=df_use_sea.index)

        return df_use_sea, df_unit
    else:
        warnings.warn(
            'SEA extension raw data file not found - '
            'SEA-Extension not included', ParserWarning)
        return None, None


def parse_oecd(path, year=None):
    """ Parse the OECD ICIO tables

    This function works for both, the 2016 and 2018 release.
    The OECd webpage provides the data as csv files in zip compressed
    archives. This function works with both, the compressed archives
    and the unpacked csv files.

    Note
    ----

    I) The original OECD ICIO tables provide some disaggregation of the Mexican
    and Chinese tables for the interindustry flows. The pymrio parser
    automatically aggregates these into Chinese And Mexican totals. Thus, the
    MX1, MX2, ..  and CN1, CN2, ... entries are aggregated into MEX and CHN.

    II) If a given storage folder contains both releases, the datafile
    must be specified in the 'path' parameter.

    Parameters
    ----------
    path: str or pathlib.Path
        Either the full path to one specific OECD ICIO file
        or the path to a storage folder with several OECD files.
        In the later case, a specific year needs to be specified.

    year: str or int, optional
        Year to parse if 'path' is given as a folder.
        If path points to a specific file, this parameter is not used.

    Returns
    -------
    IOSystem

    Raises
    ------
    ParserError
        If the file to parse could not be definitely identified.
    FileNotFoundError
        If the specified data file could not be found.

    """

    path = os.path.abspath(os.path.normpath(str(path)))

    oecd_file_starts = ['ICIO2016_', 'ICIO2018_']

    # determine which oecd file to be parsed
    if not os.path.isdir(path):
        # 1. case - one file specified in path
        oecd_file = path
        path = os.path.split(oecd_file)[0]
    else:
        # 2. case: dir given - build oecd_file with the value given in year
        if not year:
            raise ParserError('No year specified '
                              '(either specify a specific file '
                              'or path and year)')

        oecd_file_list = [
            fl for fl in os.listdir(path)
            if (os.path.splitext(fl)[1] in ['.csv', '.CSV', '.zip'] and
                os.path.splitext(fl)[0] in [oo + str(year) for oo
                                            in oecd_file_starts])]

        if len(oecd_file_list) > 1:
            unique_file_data = set([os.path.splitext(fl)[0]
                                    for fl in oecd_file_list])

            if len(unique_file_data) > 1:
                raise ParserError('Multiple files for a given year '
                                  'found (specify a specific file in the '
                                  'parameter "path")')

        elif len(oecd_file_list) == 0:
            raise FileNotFoundError('No data file for the given year found')

        oecd_file = os.path.join(path, oecd_file_list[0])

    oecd_file_name = os.path.split(oecd_file)[1]

    try:
        years = re.findall(r'\d\d\d\d', oecd_file_name)
        oecd_version = 'v' + years[0]
        oecd_year = years[1]
        meta_desc = 'OECD ICIO for {}'.format(oecd_year)

    except IndexError:
        oecd_version = 'n/a'
        oecd_year = 'n/a'
        meta_desc = 'OECD ICIO - year undefined'

    meta_rec = MRIOMetaData(location=path,
                            name='OECD-ICIO',
                            description=meta_desc,
                            version=oecd_version,
                            system='IxI',   # base don the readme
                            )

    oecd_raw = pd.read_csv(oecd_file, sep=',', index_col=0).fillna(0)
    meta_rec._add_fileio('OECD data parsed from {}'.format(oecd_file))

    mon_unit = 'Million USD'

    oecd_totals_col = ['TOTAL']
    oecd_totals_row = ['OUT', 'OUTPUT']

    oecd_raw.drop(oecd_totals_col, axis=1, errors='ignore', inplace=True)
    oecd_raw.drop(oecd_totals_row, axis=0, errors='ignore', inplace=True)

    # Important - these must not match any country or industry name
    factor_input = oecd_raw.filter(regex='VALU|TAX', axis=0)
    final_demand = oecd_raw.filter(
        regex='HFCE|NPISH|NPS|GGFC|GFCF|INVNT|INV|DIRP|FD|P33|DISC', axis=1)

    Z = oecd_raw.loc[oecd_raw.index.difference(factor_input.index),
                     oecd_raw.columns.difference(final_demand.columns)]
    F_factor_input = factor_input.loc[
        :, factor_input.columns.difference(final_demand.columns)]
    F_Y_factor_input = factor_input.loc[
        :, final_demand.columns]
    Y = final_demand.loc[final_demand.index.difference(
        F_factor_input.index), :]

    Z_index = pd.MultiIndex.from_tuples(
        tuple(ll) for ll in Z.index.str.split('_'))
    Z_columns = Z_index.copy()
    Z_index.names = IDX_NAMES['Z_row']
    Z_columns.names = IDX_NAMES['Z_col']
    Z.index = Z_index
    Z.columns = Z_columns

    _midx = []
    for orig_idx in Y.columns:
        entries = orig_idx.split('_')
        if len(entries) == 1:
            # Capturing the discrepancy column
            entries = ['ALL', entries[0]]
        if entries[1] in Z.index.get_level_values('region').unique():
            # Fixing the reversed indexing in the 2016 ICIO version
            entries = [entries[1], entries[0]]
        _midx.append(tuple(entries))
    Y.columns = pd.MultiIndex.from_tuples(_midx)
    Y.columns.names = IDX_NAMES['Y_col2']
    Y.index = Z.index

    F_factor_input.columns = Z.columns
    F_factor_input.index.names = IDX_NAMES['VA_row_single']
    F_Y_factor_input.columns = Y.columns
    F_Y_factor_input.index = F_factor_input.index

    # Aggregation of CN and MX subregions
    core_co_names = Z.columns.get_level_values('region').unique()

    agg_corr = dict(
        CHN=[a for a in core_co_names if re.match(r'CN\d', a)],
        MEX=[a for a in core_co_names if re.match(r'MX\d', a)])

    for co_name, agg_list in agg_corr.items():
        if (co_name not in core_co_names) or (len(agg_list) == 0):
            continue
        # aggregate rows
        Z.loc[co_name, :] = (Z.loc[[co_name], :] +
                             Z.loc[agg_list, :].sum(level='sector', axis=0))
        Z = Z.drop(agg_list, axis=0)
        Y.loc[co_name, :] = (Y.loc[[co_name], :] +
                             Y.loc[agg_list, :].sum(level='sector', axis=0))
        Y = Y.drop(agg_list, axis=0)

        # aggregate columns
        Z.loc[:, co_name] = (Z.loc[:, [co_name]] +
                             Z.loc[:, agg_list].sum(level='sector', axis=1))
        Z = Z.drop(agg_list, axis=1)
        F_factor_input.loc[:, co_name] = (
            F_factor_input.loc[:, [co_name]] +
            F_factor_input.loc[:, agg_list].sum(level='sector', axis=1))
        F_factor_input = F_factor_input.drop(agg_list, axis=1)

    # unit df generation at the end to have consistent index
    unit = pd.DataFrame(index=Z.index,
                        data=mon_unit,
                        columns=IDX_NAMES['unit'])
    F_unit = pd.DataFrame(index=F_factor_input.index,
                          data=mon_unit,
                          columns=IDX_NAMES['unit'])

    oecd = IOSystem(
        Z=Z,
        Y=Y,
        unit=unit,
        meta=meta_rec,
        factor_inputs={
            'name': 'factor_inputs',
            'unit': F_unit,
            'F': F_factor_input,
            'F_Y': F_Y_factor_input}
    )

    # TODO: aggregation of China and Mexico

    return oecd


def parse_eora26(path, year=None, price='bp', country_names='eora'):
    """ Parse the Eora26 database

    Note
    ----

    This parser deletes the statistical discrepancy columns from
    the parsed Eora system (reports the amount of loss in the
    meta records).

    Eora does not provide any information on the unit of the
    monetary values. Based on personal communication the unit
    is set to Mill USD manually.


    Parameters
    ----------

    path : string or pathlib.Path
       Path to the Eora raw storage folder or a specific eora zip file to
       parse.  There are several options to specify the data for parsing:

       1) Pass the name of Eora zip file. In this case the parameters 'year'
          and 'price' will not be used
       2) Pass a folder which either contains Eora zip files or unpacked Eora
          data. In that case, a year must be given
       3) Pass a folder which contains subfolders in the format 'YYYY', e.g.
          '1998' This subfolder can either contain an Eora zip file or an
          unpacked Eora system

    year : int or str
        4 digit year spec. This will not be used if a zip file
        is specified in 'path'

    price : str, optional
        'bp' or 'pp'

    country_names: str, optional
        Which country names to use:
        'eora' = Eora flavoured ISO 3 varian
        'full' = Full country names as provided by Eora
        Passing the first letter suffice.


    """
    path = os.path.abspath(os.path.normpath(str(path)))

    if country_names[0].lower() == 'e':
        country_names = 'eora'
    elif country_names[0].lower() == 'f':
        country_names = 'full'
    else:
        raise ParserError('Parameter country_names must be Eora or full')

    row_name = 'ROW'
    eora_zip_ext = '.zip'
    is_zip = False

    # determine which eora file to be parsed
    if os.path.splitext(path)[1] == eora_zip_ext:
        # case direct pass of eora zipfile
        year = re.search(r'\d\d\d\d',
                         os.path.basename(path)).group(0)
        price = re.search(r'bp|pp',
                          os.path.basename(path)).group(0)
        eora_loc = path
        root_path = os.path.split(path)[0]
        is_zip = True
    else:
        root_path = path
        if str(year) in os.listdir(path):
            path = os.path.join(path, str(year))

        eora_file_list = [fl for fl in os.listdir(path)
                          if os.path.splitext(fl)[1] == eora_zip_ext and
                          str(year) in fl and
                          str(price) in fl
                          ]

        if len(eora_file_list) > 1:
            raise ParserError('Multiple files for a given year '
                              'found (specify a specific file in parameters)')
        elif len(eora_file_list) == 1:
            eora_loc = os.path.join(path, eora_file_list[0])
            is_zip = True
        else:
            # Just a path was given, no zip file found,
            # continue with only the path information - assumed an
            # unpacked zip file
            eora_loc = path
            is_zip = False

    meta_rec = MRIOMetaData(location=root_path)

    # Eora file specs
    eora_sep = '\t'
    ZY_col = namedtuple('ZY', 'full eora system name')(0, 1, 2, 3)

    eora_files = {
        'Z': 'Eora26_{year}_{price}_T.txt'.format(
            year=str(year), price=price),
        'Q': 'Eora26_{year}_{price}_Q.txt'.format(
            year=str(year), price=price),
        'QY': 'Eora26_{year}_{price}_QY.txt'.format(
            year=str(year), price=price),
        'VA': 'Eora26_{year}_{price}_VA.txt'.format(
            year=str(year), price=price),
        'Y': 'Eora26_{year}_{price}_FD.txt'.format(
            year=str(year), price=price),
        'labels_Z': 'labels_T.txt',
        'labels_Y': 'labels_FD.txt',
        'labels_Q': 'labels_Q.txt',
        'labels_VA': 'labels_VA.txt',
    }

    header = namedtuple('header', 'index columns index_names, column_names')

    eora_header_spec = {
        'Z': header(index='labels_Z',
                    columns='labels_Z',
                    index_names=IDX_NAMES['Z_row'],
                    column_names=IDX_NAMES['Z_col'],
                    ),
        'Q': header(index='labels_Q',
                    columns='labels_Z',
                    index_names=IDX_NAMES['F_row_src'],
                    column_names=IDX_NAMES['F_col']),
        'QY': header(index='labels_Q',
                     columns='labels_Y',
                     index_names=IDX_NAMES['F_row_src'],
                     column_names=IDX_NAMES['Y_col2'],
                     ),
        'VA': header(index='labels_VA',
                     columns='labels_Z',
                     index_names=IDX_NAMES['VA_row_unit_cat'],
                     column_names=IDX_NAMES['F_col']
                     ),
        'Y': header(index='labels_Z',
                    columns='labels_Y',
                    index_names=IDX_NAMES['Y_row'],
                    column_names=IDX_NAMES['Y_col2'],
                    ),
    }

    if is_zip:
        zip_file = zipfile.ZipFile(eora_loc)
        eora_data = {
            key: pd.read_csv(
                zip_file.open(filename),
                sep=eora_sep,
                header=None,
            ) for
            key, filename in eora_files.items()}
        zip_file.close()
    else:
        eora_data = {
            key: pd.read_csv(
                os.path.join(eora_loc, filename),
                sep=eora_sep,
                header=None,
            ) for
            key, filename in eora_files.items()}
    meta_rec._add_fileio(
        'Eora26 for {year}-{price} data parsed from {loc}'.format(
            year=year, price=price, loc=eora_loc))

    eora_data['labels_Z'] = eora_data[
        'labels_Z'].loc[:, [getattr(ZY_col, country_names), ZY_col.name]]
    eora_data['labels_Y'] = eora_data[
        'labels_Y'].loc[:, [getattr(ZY_col, country_names), ZY_col.name]]
    eora_data['labels_VA'] = eora_data[
        'labels_VA'].iloc[:, :len(eora_header_spec['VA'].column_names)]
    labQ = eora_data[
        'labels_Q'].iloc[:, :len(eora_header_spec['Q'].column_names)]
    labQ.columns = IDX_NAMES['F_row_src']
    Q_unit = pd.DataFrame(
        labQ['stressor'].str.extract(r'\((.*)\)', expand=False))
    Q_unit.columns = IDX_NAMES['unit']

    labQ['stressor'] = labQ['stressor'].str.replace(r'\s\((.*)\)', '')
    eora_data['labels_Q'] = labQ

    for key in eora_header_spec.keys():
        eora_data[key].columns = (
            eora_data[eora_header_spec[key].columns].set_index(list(
                eora_data[eora_header_spec[key].columns])).index)
        eora_data[key].columns.names = eora_header_spec[key].column_names
        eora_data[key].index = (
            eora_data[eora_header_spec[key].index].set_index(list(
                eora_data[eora_header_spec[key].index])).index)
        eora_data[key].index.names = eora_header_spec[key].index_names

        try:
            meta_rec._add_modify(
                'Remove Rest of the World ({name}) '
                'row from {table} - loosing {amount}'.format(
                    name=row_name,
                    table=key,
                    amount=eora_data[key].loc[:, row_name].sum().values[0]))
            eora_data[key].drop(row_name, axis=1, inplace=True)
        except KeyError:
            pass

        try:
            meta_rec._add_modify(
                'Remove Rest of the World ({name}) column '
                'from {table} - loosing {amount}'.format(
                    name=row_name,
                    table=key,
                    amount=eora_data[key].loc[row_name, :].sum().values[0]))
            eora_data[key].drop(row_name, axis=0, inplace=True)
        except KeyError:
            pass

    Q_unit.index = eora_data['Q'].index

    meta_rec.note('Set Eora moneatry units to Mill USD manually')
    Z_unit = pd.DataFrame(data=['Mill USD'] * len(eora_data['Z'].index),
                          index=eora_data['Z'].index,
                          columns=['unit'])
    VA_unit = pd.DataFrame(data=['Mill USD'] * len(eora_data['VA'].index),
                           index=eora_data['VA'].index,
                           columns=['unit'])

    eora = IOSystem(
        Z=eora_data['Z'],
        Y=eora_data['Y'],
        unit=Z_unit,
        Q={
            'name': 'Q',
            'unit': Q_unit,
            'F': eora_data['Q'],
            'F_Y': eora_data['QY']
        },
        VA={
            'name': 'VA',
            'F': eora_data['VA'],
            'unit': VA_unit,
        },
        meta=meta_rec)

    return eora

def themis_parser(exio_files, year = None, scenario = None, themis = None, themis_caracs=None, labels=None, dlr_files=None, combo=True, compute_all=False):
    """ THEMIS parser (by adrien fabre aka. bixiou on github)

    The THEMIS model is not open. You can ask NTNU for it, they might accept.

    Two options for this parser: 1. either provide the year (2010, 2030, or 2050) and scenario ('BL' or 'BM'),
                                 2. either provide none of them, and all combinations will be loaded in a dict
    exio_files must give the path to the root folder of THEMIS
    if dlr_files is provided, the 3 Greenpeace=DLR scenarios are added (in case 2.); dlr_files = True is equivalent to dlr_files = exio_files
    if combo = True and dlr_files is provided, create 3 'combo' scenarios (improperly named): 
        2010 : ADV 2050 mix on 2010 techno (i.e. 2010 transformation matrix A); 2030: ER 2050 mix on 2010 techno; 2050: BL 2010 mix on 2050 techno
    compute_all precalculates EROIs, prices, value-added, employments
    The folder should include a file called 'Supplementary info & mixes.xlsx' which provides the IEA scenarios of energy demand.
        You can ask this file to adrien.fabre@psemail.eu
    themis_parser runs in ~1h if combo=True and compute_all=True, ~1 min if they are false
    """
    
    def load_themis(matrix='A', year=2010, scenario='BL'):
        # matrix is A, Sb, Sf or S; year is 2010, 2030 or 2050; scenario is BL or BM
        if matrix=='S': 
            Sb = load_themis('Sb', year, scenario)
            Sf = load_themis('Sf', year, scenario)       
            nb_stressors_b = Sb.shape[0] - Sf.shape[0] # check .shape for sparse
            nb_sectors_f = Sf.shape[1]
            res = sp.hstack([sp.vstack([Sf, sp.csc_matrix((nb_stressors_b, nb_sectors_f))]), Sb])
        else:
            if matrix=='A': data = 'A'
            elif matrix=='Sb': data = 'S'
            elif matrix=='Sf': data = 'S_f'
            else: data = matrix

            year = '_' + str(year)
            if data=='S_f': matrix_name = data + '_' + scenario
            else: matrix_name = data + year + '_' + scenario

            if data=='S_f': res = themis[matrix_name][:,:,2]
            else: res = themis[matrix_name]
        return(res)  
    
    def secondary_energy_demand():
        TWh2TJ = 3.60 * 1e3
        secondary_energy_demand = np.array([0]*A.shape[0])
        for name in energy_demand.index: 
            if name in list(labels['sectors']):
                secondary_energy_demand[np.where(list(map(lambda s: s == name, labels['idx_sectors'])))[0]] = \
                    energy_demand[[(reg, year) for reg in labels['regions']]].loc[name] * TWh2TJ
        return(secondary_energy_demand)
    
    if themis is None or themis_caracs is None or labels is None: 
        themis = scipy.io.loadmat(exio_files + 'Data/THEMIS2.mat')
        themis_caracs = scipy.io.loadmat(exio_files + 'Data/Characterization_endpoint2.mat')
        label = pd.read_excel(exio_files + 'Data/THEMIS2_labels.xls', header=0)
        idx_name = label['Name']
        idx_region = label['Region']
        idx_region.loc[np.where(idx_region=='AME')] = 'Africa and Middle East'
        idx_region.loc[np.where(idx_region=='CN')] = 'China'
        idx_region.loc[np.where(idx_region=='EIT')] = 'Economies in transition'
        idx_region.loc[np.where(idx_region=='IN')] = 'India'
        idx_region.loc[np.where(idx_region=='LA')] = 'Latin America'
        idx_region.loc[np.where(idx_region=='PAC')] = 'OECD Pacific'
        idx_region.loc[np.where(idx_region=='US')] = 'OECD North America'
        idx_region.loc[np.where(idx_region=='RER')] = 'OECD Europe'
        idx_region.loc[np.where(idx_region=='AS')] = 'Rest of developing Asia'
        label2 = pd.read_excel(exio_files + 'Data/THEMIS2_labels.xls', header=0, sheet_name=2)
        idx_impacts = label2['FullName']
        label3 = pd.read_excel(exio_files + 'Data/THEMIS2_labels.xls', header=0, sheet_name=3)
        idx_caracs = label3['Abbreviation THEMIS']
        labels = {'regions': idx_region.unique(), 'idx_regions': idx_region, 'impacts': idx_impacts.unique(), 'idx_impacts': idx_impacts, 
                  'sectors': idx_name.unique(), 'idx_sectors': idx_name, 'caracs': idx_caracs.unique(), 'idx_caracs': idx_caracs, 'name': 'labels'}
    if year is None and scenario is None: 
        if dlr_files is not None: 
            if combo: scenarios = ['BL', 'BM', 'REF', 'ER', 'ADV', 'combo'] 
            else: scenarios = ['BL', 'BM', 'REF', 'ER', 'ADV']
            if type(dlr_files)!=str: dlr_files = exio_files
        else: scenarios = ['BL', 'BM']
        all_themis = dict()
        for s in scenarios:
            all_themis[s] = dict()
            for y in [2010, 2030, 2050]:
                if s == 'BL' or s == 'BM': 
                    all_themis[s][y] = themis_parser(exio_files, y, s, themis, themis_caracs, labels)
                    all_themis[s][y].scenario = s
                else:
                    if s == 'combo':
                        if y in [2010, 2030]: 
                            yr, not_yr, sc = 2010, 2050, 'ADV'
                            if y == 2030: sc = 'ER'
                            all_themis[s][y] = all_themis['BL'][yr].copy(new_name='THEMIS')
                            all_themis[s][y].dlr_elec = all_themis[sc][2050].dlr_elec
                            all_themis[s][y].dlr_capacity = all_themis[sc][2050].dlr_capacity
                            global_mix = all_themis[sc][not_yr].mix(scenario = sc, path_dlr = dlr_files)[y]
                        else: 
                            yr, not_yr, sc = 2050, 2010, 'BL'
                            all_themis[s][y] = all_themis['BL'][yr].copy(new_name='THEMIS')
                            global_mix = all_themis['BL'][2010].mix_matrix(global_mix = False)
                        all_themis[s][y].aggregate_mix(mix = global_mix)
                        all_themis[s][y].change_mix(global_mix = global_mix, year = not_yr, only_exiobase = False)
                        
                    elif s == 'REF': all_themis[s][y] = all_themis['BL'][y].copy(new_name='THEMIS') # TODO: include attribute scenario
                    else: all_themis[s][y] = all_themis['BM'][y].copy(new_name='THEMIS') 
                    all_themis[s][y].scenario = s
                    if s != 'combo':
                        global_mix = all_themis[s][2010].mix(scenario = s, path_dlr = dlr_files)[y]
                        all_themis[s][y].dlr_elec = all_themis[s][2010].dlr_elec
                        all_themis[s][y].dlr_capacity = all_themis[s][2010].dlr_capacity
                        all_themis[s][y].adjust_capacity = all_themis[s][2010].adjust_capacity
                        all_themis[s][y].adjustment_capacity = all_themis[s][2010].adjustment_capacity
                        if s in ['REF', 'ER', 'ADV']: 
                            all_themis[s][y].wo_GW_adj = all_themis[s][y].copy(new_name='THEMIS')
                            all_themis[s][y].wo_GW_adj.change_mix(global_mix = global_mix, year = y, only_exiobase = False, adjust_GW = False)
                        all_themis[s][y].change_mix(global_mix = global_mix, year = y, only_exiobase = False, adjust_GW=True)
        if compute_all:
            for s in scenarios:
                for y in [2010, 2030, 2050]:                  
                    if s=='combo' and y==2050: all_themis[s][y].world_mix = all_themis[s][y].agg_mix
                    else: all_themis[s][y].world_mix = all_themis[s][y].aggregate_mix(recompute=True)
                    if s in ['REF', 'ER', 'ADV']: 
                        all_themis[s][y].wo_GW_adj.eroi_adj = all_themis[s][y].wo_GW_adj.erois(factor_elec = 2.6, \
                                                                                               recompute=True).rename(index={'Power sector': 'total'})
                        all_themis[s][y].wo_GW_adj.eroi = all_themis[s][y].wo_GW_adj.erois(recompute=True).rename(index={'Power sector': 'total'})
                        all_themis[s][y].wo_GW_adj.energy_prices()
                        all_themis[s][y].wo_GW_adj.employ_direct = all_themis[s][y].wo_GW_adj.employments()
                        all_themis[s][y].wo_GW_adj.employments(indirect = False, recompute = True)
                    all_themis[s][y].eroi_adj = all_themis[s][y].erois(factor_elec = 2.6, recompute=True).rename(index={'Power sector': 'total'})
                    all_themis[s][y].eroi = all_themis[s][y].erois(recompute=True).rename(index={'Power sector': 'total'})
                    all_themis[s][y].energy_prices()
                    all_themis[s][y].employ_direct = all_themis[s][y].employments()
                    all_themis[s][y].employments(indirect = False, recompute = True) # pb with employments combo 2050

        return(all_themis)
    elif year is None or scenario is None: print('scenario and year must be both given or None')
    else:
        A = load_themis('A', year, scenario)
        S = load_themis('S', year, scenario)
        if scenario=='BL': skip, skipfoot = 6, 67 # TODO: make a separate function the extraction of IEA scenarios
        elif scenario=='BM': skip, skipfoot = 52, 21
        energy_demand = pd.read_excel(exio_files+'Supplementary info & mixes.xlsx', \
                                         header=[0,1], index_col=0, skiprows=list(range(skip)), skipfooter=skipfoot, sheet_name=11) #TODO: select good columns>?
        energy_demand.index = ['Electricity by ' + name[0].lower() + name[1:] for name in list(energy_demand.index)]
        if scenario=='BL': skip, skipfoot = 27, 46
        if scenario=='BM': skip, skipfoot = 73, 0
        capacity = pd.read_excel(exio_files+'Supplementary info & mixes.xlsx', \
                                         header=[0,1], index_col=0, skiprows=list(range(skip)), skipfooter=skipfoot, sheet_name=11)
        capacity.index = ['Electricity by ' + name[0].lower() + name[1:] for name in list(capacity.index)] # in GW
#         C = themis_caracs['C_H_CED_22'] # midpoint characterization
#         C_large = themis_caracs['C_H_CED_large'] # midpoint characterization taken by Thomas Gibon: should be preferred to C
#         C_index = themis_caracs['EP_H_CED_22']
#         G = themis_caracs['G_H_CED_22'] # endpoint characterization
#         G_index = themis_caracs['IMP_H_CED_22'] # cf. THEMIS2_labels.xls/C_IMP for more details
#         mid2end = themis_caracs['mid2end']
        meta_rec = MRIOMetaData(system='pxp', name='THEMIS', version=scenario)
        core_data = dict()
        extensions = {'labels':labels, 'impact': {'S': S, 'name': 'impact'}, 
                      'energy':{'demand': energy_demand, 'secondary_demand': secondary_energy_demand(), 'capacity': capacity, 'name': 'energy'}} 

        return IOSystem(A=A, name='THEMIS', version=scenario, year=year, meta=meta_rec, **dict(core_data, **extensions))

def cecilia_parser(path, step = None, system='pxp'):
    """ Cecilia 2050 parser (by adrien fabre aka. bixiou on github)

    The Cecilia 2050 model is open and can be found at https://cecilia2050.eu/publications/168

    Two options for this parser: either provide the step (-1: original aggregated S & U tables, 
                                                           0: preprocessed tables, after balancing with the GRAS algorithm
                                                           1: (BAU) efficiency gains (less inputs per output (cf. EffVector), and more output (cf. GDPVector))
                                                           2a: new energy mix 
                                                           2b: (techno) technical change
                                                           3: curbing growth to respect the 2 degree scenario)
                                 either provide none of them, and all combinations will be loaded in a dict
    path must give the path to the root folder of Cecilia 2050, and files of both .zip should be placed in that folder.
    For further information, ask adrien.fabre@psemail.eu
    """
    
    def IOT_from_SUT(S, U, kind='p'): # Computes Z table
        S = np.array(S, dtype='float')
        if kind=='pxp': return(np.transpose(U.dot(div0(S,np.diag(np.sum(S, axis=1)).dot(np.ones(S.shape)))))) #sum(S).ones=sum of rows of S
        elif kind=='ixi': return(div0(S,(np.ones(S.shape)).dot(np.diag(np.sum(S, axis=0)))).dot(U)) # ones.sum(S) = sum of columns of S

    def load_matrix(matrix='U', step=0, system='pxp'):
        if type(step) == str: step_num = int(step[0])
        else: 
            step_num = step
            step = str(step)
        if step_num<=0: step = 'preprocess/'
        if step_num==-1: # TODO: other matrices
            if matrix=='U':
                M = np.loadtxt(path + 'preprocess/mrUseAggregated.txt', delimiter='\t', skiprows=2, usecols=list(range(3,519)))
            elif matrix=='V':
                M = np.loadtxt(path + 'preprocess/mrSupplyAggregated.txt', delimiter='\t', skiprows=2, usecols=list(range(3,519))) 
            elif matrix=='Y': 
                M = np.loadtxt(path + 'preprocess/mrFinalDemandAggregated.txt', delimiter='\t', skiprows=2, usecols=list(range(3,31)))
        elif step_num==0: M = np.loadtxt(path + step + matrix + '.txt', delimiter='\t')
        else: M = np.loadtxt(path + 'step' + step + '/' + matrix + 'end.txt', delimiter='\t')                
        return(M)
    
    def load_extensions():
        sectors = pd.read_excel(path + 'supply_use_tables_bau_2050.xlsx', 2, skiprows=2).iloc[0:129,2]
        regions = ['EU', 'HI', 'BX', 'WW'] # EU, High Income, Fast developing countries, RoW
        index = pd.MultiIndex.from_product([regions, sectors], names=['region', 'sector'])
        F_init = np.loadtxt(path + 'preprocess/mrMaterialsAggregated.txt', delimiter='\t', skiprows=2, usecols=tuple(range(2,518)))
        index_F = np.loadtxt(path + 'preprocess/mrMaterialsAggregated.txt', dtype='str', delimiter='\t', skiprows=2, usecols=0)
        F_init = pd.DataFrame(F_init, index = index_F, columns = index)
        return({'labels': {'regions': regions, 'sectors': sectors, 'index': index, 'name': 'labels'}, 'materials': {'F_init': F_init, 'index': index_F, 'name': 'impact'}})

    def load_system(step=0, system='pxp', x_init = None):
        S = load_matrix('V', step, system)
        U = load_matrix('U', step, system)
        y = load_matrix('Y', step, system)
        Z = IOT_from_SUT(S, U, system)
        x = y.sum(axis=1) + Z.sum(axis=1)
        A = Z.dot(np.diag(div0(1,x)))
        L = np.linalg.inv(np.eye(A.shape[0])-A)
        index = extensions['labels']['index']
        index_y = pd.MultiIndex.from_product([extensions['labels']['regions'], ['Final consumption expenditure by households', \
            'Final consumption expenditure by non-profit organisations serving households (NPISH)', 'Final consumption expenditure by government', \
            'Gross fixed capital formation', 'Changes in inventories', 'Changes in valuables', 'Export']], names=['region', 'sector'])
        if step==-1: x_init = x
        elif x_init is None: 
            S_init = load_matrix('V', -1, system)
            U_init = load_matrix('U', -1, system)
            y_init = load_matrix('Y', -1, system)
            Z_init = IOT_from_SUT(S_init, U_init, system)
            x_init = y_init.sum(axis=1) + Z_init.sum(axis=1)            
        extensions['materials'].update({'S': pd.DataFrame(div0(extensions['materials']['F_init'], x_init), index = extensions['materials']['index'], columns = index)})
        extensions['materials'].update({'F': pd.DataFrame(extensions['materials']['S'] * x, index = extensions['materials']['index'], columns = index)})
        Z = pd.DataFrame(Z, index = index, columns = index)
        Y = pd.DataFrame(y, index = index, columns = index_y)
        x = pd.Series(x, index = index)

        meta_rec = MRIOMetaData(system=system, name='Cecilia', version=step)
        if step==0 or step==-1: year = 2000
        else: year = 2050
        core_data = dict()
        return IOSystem(A=A, Z=Z, Y=Y, x=x, L=L, name='Cecilia', version=step, year=year, meta=meta_rec, **dict(core_data, **extensions))
    x_init = None
    extensions = load_extensions()
    if step is None:
        cecilias = dict()
        for step in [-1, 0, 1, '2a', '2b', 3]: cecilias[step] = load_system(step, system, x_init)
        if step==-1: x_init = cecilias[-1].x
        return(cecilias)
    else: return(load_system(step, system, x_init))