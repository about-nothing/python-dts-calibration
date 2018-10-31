# coding=utf-8
import os

import dask.array as da
import numpy as np
import pandas as pd

# Returns a dictionary with the attributes to the dimensions. The keys refer to the namin
#     gin used in the raw files.
_dim_attrs = {
    ('x', 'distance'):          {
        'name':             'distance',
        'description':      'Length along fiber',
        'long_description': 'Starting at connector of forward channel',
        'units':            'm'},
    ('TMP', 'temperature'):     {
        'name':        'TMP',
        'description': 'temperature calibrated by device',
        'units':       'degC'},
    ('ST',):                    {
        'name':        'ST',
        'description': 'Stokes intensity',
        'units':       '-'},
    ('AST',):                   {
        'name':        'AST',
        'description': 'anti-Stokes intensity',
        'units':       '-'},
    ('REV-ST',):                {
        'name':        'REV-ST',
        'description': 'reverse Stokes intensity',
        'units':       '-'},
    ('REV-AST',):               {
        'name':        'REV-AST',
        'description': 'reverse anti-Stokes intensity',
        'units':       '-'},
    ('acquisitionTime',):       {
        'name':             'acquisitionTime',
        'description':      'Measurement duration of forward channel',
        'long_description': 'Actual measurement duration of forward channel',
        'units':            'seconds'},
    ('userAcquisitionTimeFW',): {
        'name':             'userAcquisitionTimeFW',
        'description':      'Measurement duration of forward channel',
        'long_description': 'Desired measurement duration of forward channel',
        'units':            'seconds'},
    ('userAcquisitionTimeBW',): {
        'name':             'userAcquisitionTimeBW',
        'description':      'Measurement duration of backward channel',
        'long_description': 'Desired measurement duration of backward channel',
        'units':            'seconds'},
    }

# Because variations in the names exist between the different file
#     formats. The tuple as key contains the possible keys, which is expanded below.
dim_attrs = {k: v for kl, v in _dim_attrs.items() for k in kl}


def silixa_xml_version_check(filepathlist):
    """Function which tests which version of xml files have to be read.

    Parameters
    ----------
    filepathlist

    Returns
    -------

    """

    sep = ':'
    attrs = read_silixa_attrs_singlefile(filepathlist[0], sep)

    version_string = attrs['customData:SystemSettings:softwareVersion']

    # Get major version from string. Tested for Ultima v4, v6, XT-DTS v6
    major_version = int(version_string.replace(' ', '').split(':')[-1][0])

    return major_version


def read_silixa_files_routine_v6(filepathlist,
                                 timezone_netcdf='UTC',
                                 timezone_input_files='UTC',
                                 silent=False,
                                 load_in_memory='auto'):
    """
    Internal routine that reads Silixa files. Use dtscalibration.read_silixa_files function instead.

    Parameters
    ----------
    filepathlist
    timezone_netcdf
    timezone_input_files
    silent

    Returns
    -------

    """
    import dask
    from xml.etree import ElementTree

    sep = ':'
    ns = {'s': 'http://www.witsml.org/schemas/1series'}

    # Obtain metadata from the first file
    attrs = read_silixa_attrs_singlefile(filepathlist[0], sep)

    # Add standardised required attributes
    attrs['isDoubleEnded'] = attrs['customData:isDoubleEnded']
    attrs['forwardMeasurementChannel'] = attrs['customData:forwardMeasurementChannel']
    attrs['reverseMeasurementChannel'] = attrs['customData:reverseMeasurementChannel']

    # obtain basic data info
    data_item_names = attrs['logData:mnemonicList'].replace(" ", "").strip(' ').split(',')
    nitem = len(data_item_names)

    x_start = np.float32(attrs['startIndex:#text'])
    x_end = np.float32(attrs['endIndex:#text'])
    dx = np.float32(attrs['stepIncrement:#text'])
    nx = int((x_end - x_start) / dx)

    ntime = len(filepathlist)

    double_ended_flag = bool(int(attrs['isDoubleEnded']))
    chFW = int(attrs['forwardMeasurementChannel']) - 1  # zero-based
    if double_ended_flag:
        chBW = int(attrs['reverseMeasurementChannel']) - 1  # zero-based
    else:
        # no backward channel is negative value. writes better to netcdf
        chBW = -1

    # print summary
    if not silent:
        print('%s files were found, each representing a single timestep' % ntime)
        print('%s recorded vars were found: ' % nitem + ', '.join(data_item_names))
        print('Recorded at %s points along the cable' % nx)

        if double_ended_flag:
            print('The measurement is double ended')
        else:
            print('The measurement is single ended')

    # obtain timeseries from data
    timeseries_loc_in_hierarchy = [
        ('log', 'customData', 'acquisitionTime'),
        ('log', 'customData', 'referenceTemperature'),
        ('log', 'customData', 'probe1Temperature'),
        ('log', 'customData', 'probe2Temperature'),
        ('log', 'customData', 'referenceProbeVoltage'),
        ('log', 'customData', 'probe1Voltage'),
        ('log', 'customData', 'probe2Voltage'),
        ('log', 'customData', 'UserConfiguration',
         'ChannelConfiguration', 'AcquisitionConfiguration',
         'AcquisitionTime', 'userAcquisitionTimeFW')
        ]

    if double_ended_flag:
        timeseries_loc_in_hierarchy.append(
            ('log', 'customData', 'UserConfiguration',
             'ChannelConfiguration', 'AcquisitionConfiguration',
             'AcquisitionTime', 'userAcquisitionTimeBW'))

    timeseries = {
        item[-1]: dict(loc=item, array=np.zeros(ntime, dtype=np.float32))
        for item in timeseries_loc_in_hierarchy
        }

    # add units to timeseries (unit of measurement)
    for key, item in timeseries.items():
        if f'customData:{key}:uom' in attrs:
            item['uom'] = attrs[f'customData:{key}:uom']
        else:
            item['uom'] = ''

    # Gather data
    arr_path = 's:' + '/s:'.join(['log', 'logData', 'data'])

    @dask.delayed
    def grab_data_per_file(file_handle):
        with open(file_handle, 'r') as f_h:
            eltree = ElementTree.parse(f_h)
            arr_el = eltree.findall(arr_path, namespaces=ns)

            # remove the breaks on both sides of the string
            # split the string on the comma
            arr_str = [arr_eli.text[1:-1].split(',') for arr_eli in arr_el]

        return np.array(arr_str, dtype=np.float)

    data_lst_dly = [grab_data_per_file(fp) for fp in filepathlist]
    data_lst = [da.from_delayed(x, shape=(nx, nitem), dtype=np.float) for x in data_lst_dly]
    data_arr = da.stack(data_lst).T  # .compute()

    # Check whether to compute data_arr (if possible 25% faster)
    data_arr_cnk = data_arr.rechunk({0: -1, 1: -1, 2: 'auto'})
    if load_in_memory == 'auto' and data_arr_cnk.npartitions <= 5:
        data_arr = data_arr_cnk.compute()
    elif load_in_memory:
        data_arr = data_arr_cnk.compute()
    else:
        data_arr = data_arr_cnk

    data_vars = {}
    for name, data_arri in zip(data_item_names, data_arr):
        if name == 'LAF':
            continue

        if name in dim_attrs:
            data_vars[name] = (['x', 'time'], data_arri, dim_attrs[name])

        else:
            raise ValueError('Dont know what to do with the {} data column'.format(name))

    # Obtaining the timeseries data (reference temperature etc)
    _ts_dtype = [(k, np.float32) for k in timeseries]
    _time_dtype = [('filename_tstamp', np.int64),
                   ('minDateTimeIndex', '<U29'),
                   ('maxDateTimeIndex', '<U29')]
    ts_dtype = np.dtype(_ts_dtype + _time_dtype)

    @dask.delayed
    def grab_timeseries_per_file(file_handle):
        with open(file_handle, 'r') as f_h:
            eltree = ElementTree.parse(f_h)

            out = []
            for k, v in timeseries.items():
                # Get all the timeseries data
                if 'userAcquisitionTimeFW' in v['loc']:
                    # requires two namespace searches
                    path1 = 's:' + '/s:'.join(v['loc'][:4])
                    val1 = eltree.findall(path1, namespaces=ns)
                    path2 = 's:' + '/s:'.join(v['loc'][4:6])
                    val2 = val1[chFW].find(path2, namespaces=ns)
                    out.append(val2.text)

                elif 'userAcquisitionTimeBW' in v['loc']:
                    # requires two namespace searches
                    path1 = 's:' + '/s:'.join(v['loc'][:4])
                    val1 = eltree.findall(path1, namespaces=ns)
                    path2 = 's:' + '/s:'.join(v['loc'][4:6])
                    val2 = val1[chBW].find(path2, namespaces=ns)
                    out.append(val2.text)

                else:
                    path = 's:' + '/s:'.join(v['loc'])
                    val = eltree.find(path, namespaces=ns)
                    out.append(val.text)

            # get all the time related data
            startDateTimeIndex = eltree.find(
                's:log/s:startDateTimeIndex', namespaces=ns).text
            endDateTimeIndex = eltree.find(
                's:log/s:endDateTimeIndex', namespaces=ns).text

            file_name = os.path.split(file_handle)[1]
            tstamp = np.int64(file_name[10:27])

            out += [tstamp, startDateTimeIndex, endDateTimeIndex]
        return np.array(tuple(out), dtype=ts_dtype)

    ts_lst_dly = [grab_timeseries_per_file(fp) for fp in filepathlist]
    ts_lst = [da.from_delayed(x, shape=tuple(), dtype=ts_dtype) for x in ts_lst_dly]
    ts_arr = da.stack(ts_lst).compute()

    for name in timeseries:
        if name in dim_attrs:
            data_vars[name] = (('time',), ts_arr[name], dim_attrs[name])

        else:
            data_vars[name] = (('time',), ts_arr[name])

    # construct the coordinate dictionary
    coords = {
        'x':        ('x', data_arr[0, :, 0], dim_attrs['x']),
        'filename': ('time', [os.path.split(f)[1] for f in filepathlist]),
        'filename_tstamp': ('time', ts_arr['filename_tstamp'])}

    maxTimeIndex = pd.DatetimeIndex(ts_arr['maxDateTimeIndex'])
    dtFW = ts_arr['userAcquisitionTimeFW'].astype('timedelta64[s]')

    if not double_ended_flag:
        tcoords = coords_time(maxTimeIndex, timezone_netcdf, timezone_input_files,
                              dtFW=dtFW, double_ended_flag=double_ended_flag)
    else:
        dtBW = ts_arr['userAcquisitionTimeBW'].astype('timedelta64[s]')
        tcoords = coords_time(maxTimeIndex, timezone_netcdf, timezone_input_files,
                              dtFW=dtFW, dtBW=dtBW, double_ended_flag=double_ended_flag)

    coords.update(tcoords)

    return data_vars, coords, attrs


def read_silixa_files_routine_v4(filepathlist,
                                 timezone_netcdf='UTC',
                                 timezone_input_files='UTC',
                                 silent=False,
                                 load_in_memory='auto'):
    """
    Internal routine that reads Silixa files. Use dtscalibration.read_silixa_files function instead.

    Parameters
    ----------
    filepathlist
    timezone_netcdf
    timezone_input_files
    silent

    Returns
    -------

    """
    import dask
    from xml.etree import ElementTree

    sep = ':'
    ns = {'s': 'http://www.witsml.org/schemas/1series'}

    # Obtain metadata from the first file
    attrs = read_silixa_attrs_singlefile(filepathlist[0], sep)

    # Add standardised required attributes
    attrs['isDoubleEnded'] = attrs['customData:isDoubleEnded']
    attrs['forwardMeasurementChannel'] = attrs['customData:forwardMeasurementChannel']
    attrs['reverseMeasurementChannel'] = attrs['customData:reverseMeasurementChannel']

    double_ended_flag = bool(int(attrs['isDoubleEnded']))
    chFW = int(attrs['forwardMeasurementChannel']) - 1  # zero-based
    if double_ended_flag:
        chBW = int(attrs['reverseMeasurementChannel']) - 1  # zero-based
    else:
        # no backward channel is negative value. writes better to netcdf
        chBW = -1

    # obtain basic data info
    if double_ended_flag:
        data_item_names = [attrs['logCurveInfo_{0}:mnemonic'.format(x)] for x in range(0, 6)]
    else:
        data_item_names = [attrs['logCurveInfo_{0}:mnemonic'.format(x)] for x in range(0, 4)]

    nitem = len(data_item_names)

    x_start = np.float32(attrs['blockInfo:startIndex:#text'])
    x_end = np.float32(attrs['blockInfo:endIndex:#text'])
    dx = np.float32(attrs['blockInfo:stepIncrement:#text'])
    nx = int((x_end - x_start) / dx)

    ntime = len(filepathlist)

    # print summary
    if not silent:
        print('%s files were found, each representing a single timestep' % ntime)
        print('%s recorded vars were found: ' % nitem + ', '.join(data_item_names))
        print('Recorded at %s points along the cable' % nx)

        if double_ended_flag:
            print('The measurement is double ended')
        else:
            print('The measurement is single ended')

    # obtain timeseries from data
    timeseries_loc_in_hierarchy = [
        ('wellLog', 'customData', 'acquisitionTime'),
        ('wellLog', 'customData', 'referenceTemperature'),
        ('wellLog', 'customData', 'probe1Temperature'),
        ('wellLog', 'customData', 'probe2Temperature'),
        ('wellLog', 'customData', 'referenceProbeVoltage'),
        ('wellLog', 'customData', 'probe1Voltage'),
        ('wellLog', 'customData', 'probe2Voltage'),
        ('wellLog', 'customData', 'UserConfiguration',
         'ChannelConfiguration', 'AcquisitionConfiguration',
         'AcquisitionTime', 'userAcquisitionTimeFW')
        ]

    if double_ended_flag:
        timeseries_loc_in_hierarchy.append(
            ('wellLog', 'customData', 'UserConfiguration',
             'ChannelConfiguration', 'AcquisitionConfiguration',
             'AcquisitionTime', 'userAcquisitionTimeBW'))

    timeseries = {
        item[-1]: dict(loc=item, array=np.zeros(ntime, dtype=np.float32))
        for item in timeseries_loc_in_hierarchy
        }

    # add units to timeseries (unit of measurement)
    for key, item in timeseries.items():
        if f'customData:{key}:uom' in attrs:
            item['uom'] = attrs[f'customData:{key}:uom']
        else:
            item['uom'] = ''

    # Gather data
    arr_path = 's:' + '/s:'.join(['wellLog', 'logData', 'data'])

    @dask.delayed
    def grab_data_per_file(file_handle):
        with open(file_handle, 'r') as f_h:
            eltree = ElementTree.parse(f_h)
            arr_el = eltree.findall(arr_path, namespaces=ns)

            # remove the breaks on both sides of the string
            # split the string on the comma
            arr_str = [arr_eli.text.split(',') for arr_eli in arr_el]
        return np.array(arr_str, dtype=float)

    data_lst_dly = [grab_data_per_file(fp) for fp in filepathlist]
    data_lst = [da.from_delayed(x, shape=(nx, nitem), dtype=np.float) for x in data_lst_dly]
    data_arr = da.stack(data_lst).T  # .compute()

    # Check whether to compute data_arr (if possible 25% faster)
    data_arr_cnk = data_arr.rechunk({0: -1, 1: -1, 2: 'auto'})
    if load_in_memory == 'auto' and data_arr_cnk.npartitions <= 5:
        data_arr = data_arr_cnk.compute()
    elif load_in_memory:
        data_arr = data_arr_cnk.compute()
    else:
        data_arr = data_arr_cnk

    data_vars = {}
    for name, data_arri in zip(data_item_names, data_arr):
        if name == 'LAF':
            continue

        if name in dim_attrs:
            data_vars[name] = (['x', 'time'], data_arri, dim_attrs[name])

        else:
            raise ValueError('Dont know what to do with the {} data column'.format(name))

    # Obtaining the timeseries data (reference temperature etc)
    _ts_dtype = [(k, np.float32) for k in timeseries]
    _time_dtype = [('filename_tstamp', np.int64),
                   ('minDateTimeIndex', '<U29'),
                   ('maxDateTimeIndex', '<U29')]
    ts_dtype = np.dtype(_ts_dtype + _time_dtype)

    @dask.delayed
    def grab_timeseries_per_file(file_handle):
        with open(file_handle, 'r') as f_h:
            eltree = ElementTree.parse(f_h)

            out = []
            for k, v in timeseries.items():
                # Get all the timeseries data
                if 'userAcquisitionTimeFW' in v['loc']:
                    # requires two namespace searches
                    path1 = 's:' + '/s:'.join(v['loc'][:4])
                    val1 = eltree.findall(path1, namespaces=ns)
                    path2 = 's:' + '/s:'.join(v['loc'][4:6])
                    val2 = val1[chFW].find(path2, namespaces=ns)
                    out.append(val2.text)

                elif 'userAcquisitionTimeBW' in v['loc']:
                    # requires two namespace searches
                    path1 = 's:' + '/s:'.join(v['loc'][:4])
                    val1 = eltree.findall(path1, namespaces=ns)
                    path2 = 's:' + '/s:'.join(v['loc'][4:6])
                    val2 = val1[chBW].find(path2, namespaces=ns)
                    out.append(val2.text)

                else:
                    path = 's:' + '/s:'.join(v['loc'])
                    val = eltree.find(path, namespaces=ns)
                    out.append(val.text)

            # get all the time related data
            startDateTimeIndex = eltree.find(
                's:wellLog/s:minDateTimeIndex', namespaces=ns).text
            endDateTimeIndex = eltree.find(
                's:wellLog/s:maxDateTimeIndex', namespaces=ns).text

            file_name = os.path.split(file_handle)[1]
            tstamp = np.int64(file_name[10:-4])

            out += [tstamp, startDateTimeIndex, endDateTimeIndex]
        return np.array(tuple(out), dtype=ts_dtype)

    ts_lst_dly = [grab_timeseries_per_file(fp) for fp in filepathlist]
    ts_lst = [da.from_delayed(x, shape=tuple(), dtype=ts_dtype) for x in ts_lst_dly]
    ts_arr = da.stack(ts_lst).compute()

    for name in timeseries:
        if name in dim_attrs:
            data_vars[name] = (('time',), ts_arr[name], dim_attrs[name])

        else:
            data_vars[name] = (('time',), ts_arr[name])

    # construct the coordinate dictionary
    coords = {
        'x':        ('x', data_arr[0, :, 0], dim_attrs['x']),
        'filename': ('time', [os.path.split(f)[1] for f in filepathlist]),
        'filename_tstamp': ('time', ts_arr['filename_tstamp'])}

    maxTimeIndex = pd.DatetimeIndex(ts_arr['maxDateTimeIndex'])
    dtFW = ts_arr['userAcquisitionTimeFW'].astype('timedelta64[s]')

    if not double_ended_flag:
        tcoords = coords_time(maxTimeIndex, timezone_netcdf, timezone_input_files,
                              dtFW=dtFW, double_ended_flag=double_ended_flag)
    else:
        dtBW = ts_arr['userAcquisitionTimeBW'].astype('timedelta64[s]')
        tcoords = coords_time(maxTimeIndex, timezone_netcdf, timezone_input_files,
                              dtFW=dtFW, dtBW=dtBW, double_ended_flag=double_ended_flag)

    coords.update(tcoords)

    return data_vars, coords, attrs


def read_sensornet_files_routine_v3(filepathlist,
                                    timezone_netcdf='UTC',
                                    timezone_input_files='UTC',
                                    silent=False):
    """
    Internal routine that reads Sensor files.
    Use dtscalibration.read_sensornet_files function instead.

    Parameters
    ----------
    filepathlist
    timezone_netcdf
    timezone_input_files
    silent

    Returns
    -------

    """

    # Obtain metadata from the first file
    data, meta = read_sensornet_single(filepathlist[0])

    # Pop keys from the meta dict which are variable over time
    popkeys = ('T ext. ref 1 (°C)',
               'T ext. ref 2 (°C)',
               'T internal ref (°C)',
               'date',
               'time',
               'gamma',
               'k internal',
               'k external')
    [meta.pop(key) for key in popkeys]
    attrs = meta

    # Add standardised required attributes
    attrs['isDoubleEnded'] = str(1 - (meta['differential loss correction']
                                      == 'single-ended'))
    double_ended_flag = bool(int(attrs['isDoubleEnded']))

    attrs['forwardMeasurementChannel'] = meta['forward channel'][-1]
    if double_ended_flag:
        attrs['reverseMeasurementChannel'] = 'N/A'
    else:
        attrs['reverseMeasurementChannel'] = meta['reverse channel'][-1]

    # obtain basic data info
    nx = data['x'].size

    ntime = len(filepathlist)

    # chFW = int(attrs['forwardMeasurementChannel']) - 1  # zero-based
    # if double_ended_flag:
    #     chBW = int(attrs['reverseMeasurementChannel']) - 1  # zero-based
    # else:
    #     # no backward channel is negative value. writes better to netcdf
    #     chBW = -1

    # print summary
    if not silent:
        print('%s files were found,' % ntime +
              ' each representing a single timestep')
        print('Recorded at %s points along the cable' % nx)

        if double_ended_flag:
            print('The measurement is double ended')
        else:
            print('The measurement is single ended')

    #   Gather data
    # x has already been read. should not change over time
    x = data['x']

    # Define all variables
    referenceTemperature = np.zeros(ntime)
    probe1temperature = np.zeros(ntime)
    probe2temperature = np.zeros(ntime)
    gamma_ddf = np.zeros(ntime)
    k_internal = np.zeros(ntime)
    k_external = np.zeros(ntime)
    acquisitiontimeFW = np.zeros(ntime)
    acquisitiontimeBW = np.zeros(ntime)

    timestamp = ['']*ntime
    ST = np.zeros((nx, ntime))
    AST = np.zeros((nx, ntime))
    TMP = np.zeros((nx, ntime))

    if double_ended_flag:
        REV_ST = np.zeros((nx, ntime))
        REV_AST = np.zeros((nx, ntime))

    for ii in range(ntime):
        data, meta = read_sensornet_single(filepathlist[ii])

        timestamp[ii] = pd.DatetimeIndex([meta['date']+' '+meta['time']])[0]
        probe1temperature[ii] = float(meta['T ext. ref 1 (°C)'])
        probe2temperature[ii] = float(meta['T ext. ref 2 (°C)'])
        referenceTemperature[ii] = float(meta['T internal ref (°C)'])
        gamma_ddf[ii] = float(meta['gamma'])
        k_internal[ii] = float(meta['k internal'])
        k_external[ii] = float(meta['k external'])
        acquisitiontimeFW[ii] = float(meta['forward acquisition time'])
        acquisitiontimeBW[ii] = float(meta['reverse acquisition time'])

        ST[:, ii] = data['ST']
        AST[:, ii] = data['AST']
        TMP[:, ii] = data['TMP']

        if double_ended_flag:
            REV_ST[:, ii] = data['REV_ST']
            REV_AST[:, ii] = data['REV_AST']

    data_vars = {'ST': (['x', 'time'], ST, dim_attrs['ST']),
                 'AST': (['x', 'time'], AST, dim_attrs['AST']),
                 'TMP': (['x', 'time'], TMP, dim_attrs['TMP']),
                 'probe1Temperature': ('time',
                                       probe1temperature,
                                       {'name': 'Probe 1 temperature',
                                        'description':
                                            'reference probe 1 temperature',
                                        'units': 'degC'}),
                 'probe2Temperature': ('time',
                                       probe2temperature,
                                       {'name': 'Probe 2 temperature',
                                        'description':
                                            'reference probe 2 temperature',
                                        'units': 'degC'}),
                 'referenceTemperature': ('time',
                                          referenceTemperature,
                                          {'name': 'reference temperature',
                                           'description':
                                              'Internal reference temperature',
                                           'units': 'degC'}),
                 'gamma_ddf': ('time',
                               gamma_ddf,
                               {'name': 'gamma ddf',
                                'description': 'machine calibrated gamma',
                                'units': '-'}),
                 'k_internal': ('time',
                                k_internal,
                                {'name': 'k internal',
                                 'description':
                                     'machine calibrated internal k',
                                 'units': '-'}),
                 'k_external': ('time',
                                k_external,
                                {'name': 'reference temperature',
                                 'description':
                                     'machine calibrated external k',
                                 'units': '-'}),
                 'userAcquisitionTimeFW': ('time',
                                           acquisitiontimeFW,
                                           dim_attrs['userAcquisitionTimeFW']),
                 'userAcquisitionTimeBW': ('time',
                                           acquisitiontimeBW,
                                           dim_attrs['userAcquisitionTimeBW'])}

    if double_ended_flag:
        data_vars['REV-ST'] = (['x', 'time'], REV_ST, dim_attrs['REV-ST'])
        data_vars['REV-AST'] = (['x', 'time'], REV_AST, dim_attrs['REV-AST'])

    filenamelist = [os.path.split(f)[-1] for f in filepathlist]

    coords = {'x':        ('x', x, dim_attrs['x']),
              'filename': ('time', filenamelist)}

    dtFW = data_vars['userAcquisitionTimeFW'][1].astype('timedelta64[s]')
    dtBW = data_vars['userAcquisitionTimeBW'][1].astype('timedelta64[s]')
    if not double_ended_flag:
        tcoords = coords_time(np.array(timestamp).astype('datetime64[ns]'),
                              timezone_netcdf,
                              timezone_input_files, dtFW=dtFW,
                              double_ended_flag=double_ended_flag)
    else:
        tcoords = coords_time(np.array(timestamp).astype('datetime64[ns]'),
                              timezone_netcdf,
                              timezone_input_files, dtFW=dtFW, dtBW=dtBW,
                              double_ended_flag=double_ended_flag)

    coords.update(tcoords)

    return data_vars, coords, attrs


def read_silixa_attrs_singlefile(filename, sep):
    import xmltodict

    def metakey(meta, dict_to_parse, prefix, sep):
        """
        Fills the metadata dictionairy with data from dict_to_parse.
        The dict_to_parse is the raw data from a silixa xml-file.
        dict_to_parse is a nested dictionary to represent the
        different levels of hierarchy. For example,
        toplevel = {lowlevel: {key: value}}.
        This function returns {'toplevel:lowlevel:key': value}.
        Where prefix is the flattened hierarchy.

        Parameters
        ----------
        meta : dict
            the output dictionairy with prcessed metadata
        dict_to_parse : dict

        prefix
        sep

        Returns
        -------

        """

        for key in dict_to_parse:
            if prefix == "":

                prefix_parse = key.replace('@', '')
            else:
                prefix_parse = sep.join([prefix, key.replace('@', '')])

            if prefix_parse == sep.join(('logData', 'data')):
                # skip the LAF , ST data
                continue

            if hasattr(dict_to_parse[key], 'keys'):
                # Nested dictionaries, flatten hierarchy.
                meta.update(metakey(meta,
                                    dict_to_parse[key],
                                    prefix_parse,
                                    sep))

            elif isinstance(dict_to_parse[key], list):
                # if the key has values for the multiple channels
                for ival, val in enumerate(dict_to_parse[key]):
                    num_key = prefix_parse + '_' + str(ival)
                    meta.update(metakey(meta, val, num_key, sep))
            else:

                meta[prefix_parse] = dict_to_parse[key]

        return meta

    with open(filename) as fh:
        doc_ = xmltodict.parse(fh.read())

    if u'wellLogs' in doc_.keys():
        doc = doc_[u'wellLogs'][u'wellLog']
    else:
        doc = doc_[u'logs'][u'log']

    return metakey(dict(), doc, '', sep)


def read_sensornet_single(filename):
    headerlength = 26

    with open(filename) as fileobject:
        filelength = sum([1 for line in fileobject])
    datalength = filelength - headerlength

    meta = {}
    with open(filename) as fileobject:
        for ii in range(0, headerlength - 1):
            fileline = fileobject.readline().split('\t')

            meta[fileline[0]] = fileline[1].replace('\n', '').replace(',', '.')

        # data_names =
        fileobject.readline().split('\t')

        if meta['differential loss correction'] == 'single-ended':
            data = {'x': np.zeros(datalength),
                    'TMP': np.zeros(datalength),
                    'ST': np.zeros(datalength),
                    'AST': np.zeros(datalength)}

            for ii in range(0, datalength):
                fileline = fileobject.readline().replace(',', '.').split('\t')

                data['x'][ii] = float(fileline[0])
                data['TMP'][ii] = float(fileline[1])
                data['ST'][ii] = float(fileline[2])
                data['AST'][ii] = float(fileline[3])

        else:
            raise NotImplementedError('double-ended DDF files ' +
                                      'are not implemented yet')

    return data, meta


def coords_time(maxTimeIndex, timezone_input_files, timezone_netcdf='UTC', dtFW=None,
                dtBW=None, double_ended_flag=False):
    """
    Prepares the time coordinates for the construction of DataStore instances with metadata

    Parameters
    ----------
    maxTimeIndex : array-like (1-dimensional)
        Is an array with 'datetime64[ns]' timestamps of the end of the
        forward channel. If single ended this is the end of the measurement.
        If double ended this is halfway the double ended measurement.
    timezone_input_files : string, pytz.timezone, dateutil.tz.tzfile or None
        A string of a timezone that is understood by pandas of maxTimeIndex.
    timezone_netcdf : string, pytz.timezone, dateutil.tz.tzfile or None
        A string of a timezone that is understood by pandas to write the netCDF to. Using UTC as
        default, according to CF conventions.
    dtFW : array-like (1-dimensional) of float
        The acquisition time of the Forward channel in seconds
    dtBW : array-like (1-dimensional) of float
        The acquisition time of the Backward channel in seconds
    double_ended_flag : bool
        A flag whether the measurement is double ended

    Returns
    -------

    """
    time_attrs = {
        'time':        {
            'description': 'time halfway the measurement',
            'timezone':    str(timezone_netcdf)},
        'timestart':   {
            'description': 'time start of the measurement',
            'timezone':    str(timezone_netcdf)},
        'timeend':     {
            'description': 'time end of the measurement',
            'timezone':    str(timezone_netcdf)},
        'timeFW':      {
            'description': 'time halfway the forward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeFWstart': {
            'description': 'time start of the forward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeFWend':   {
            'description': 'time end of the forward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeBW':      {
            'description': 'time halfway the backward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeBWstart': {
            'description': 'time start of the backward channel measurement',
            'timezone':    str(timezone_netcdf)},
        'timeBWend':   {
            'description': 'time end of the backward channel measurement',
            'timezone':    str(timezone_netcdf)},
        }

    if not double_ended_flag:
        # single ended measurement
        dt1 = dtFW.astype('timedelta64[s]')

        # start of the forward measurement
        index_time_FWstart = maxTimeIndex - dt1

        # end of the forward measurement
        index_time_FWend = maxTimeIndex

        # center of forward measurement
        index_time_FWmean = maxTimeIndex - dt1 / 2

        coords_zip = [('timestart', index_time_FWstart),
                      ('timeend', index_time_FWend),
                      ('time', index_time_FWmean)]

    else:
        # double ended measurement
        dt1 = dtFW.astype('timedelta64[s]')
        dt2 = dtBW.astype('timedelta64[s]')

        # start of the forward measurement
        index_time_FWstart = maxTimeIndex - dt1

        # end of the forward measurement
        index_time_FWend = maxTimeIndex

        # center of forward measurement
        index_time_FWmean = maxTimeIndex - dt1 / 2

        # start of the backward measurement
        index_time_BWstart = index_time_FWend.copy()

        # end of the backward measurement
        index_time_BWend = maxTimeIndex + dt2

        # center of backward measurement
        index_time_BWmean = maxTimeIndex + dt2 / 2

        coords_zip = [('timeFWstart', index_time_FWstart),
                      ('timeFWend', index_time_FWend),
                      ('timeFW', index_time_FWmean),
                      ('timeBWstart', index_time_BWstart),
                      ('timeBWend', index_time_BWend),
                      ('timeBW', index_time_BWmean),
                      ('timestart', index_time_FWstart),
                      ('timeend', index_time_BWend),
                      ('time', index_time_FWend)]

    coords = {k: (
        'time',
        pd.DatetimeIndex(v).tz_localize(
            tz=timezone_input_files).tz_convert(
            timezone_netcdf).astype('datetime64[ns]'),
        time_attrs[k]) for k, v in coords_zip
        }

    # The units are already stored in the dtype
    coords['acquisitiontimeFW'] = (
        'time', dt1,
        {'description': 'Acquisition time of the forward measurement'})

    if double_ended_flag:
        # The units are already stored in the dtype
        coords['acquisitiontimeBW'] = (
            'time', dt2,
            {'description': 'Acquisition time of the backward measurement'})

    return coords
