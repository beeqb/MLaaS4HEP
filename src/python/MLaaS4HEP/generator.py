#!/usr/bin/env python
#-*- coding: utf-8 -*-
#pylint: disable=
"""
File       : generator.py
Author     : Valentin Kuznetsov <vkuznet AT gmail dot com>
Description: defines DataGenerator for MLaaS4HEP
"""

# system modules
import os
import json
import time
import random

# numpy modules
import numpy as np

# MLaaS4HEP modules
from MLaaS4HEP.reader import RootDataReader, JsonReader, CsvReader, AvroReader, ParquetReader
from MLaaS4HEP.utils import file_type, timestamp


class MetaDataGenerator(object):
    """
    MetaDataGenerator class provides interface to read files.
    """
    def __init__(self, fin, labels, params=None, specs=None, preproc=None, dtype=None):
        "Initialization function for Data Generator"
        time0 = time.time()
        self.dtype = str(dtype).lower()
        self.preproc = preproc
        if not params:
            params = {}
        # parse given parameters
        batch_size = params.get('batch_size', 256)
        self.verbose = params.get('verbose', 0)
        chunk_size = params.get('chunk_size', 1000)
        self.evts = params.get('nevts', -1)
        self.shuffle = params.get('shuffle', False)

        # convert input fin parameter into file list if necessary
        if isinstance(fin, str):
            self.files = [fin]
        elif isinstance(fin, list):
            self.files = fin
        else:
            raise Exception("Unsupported data-type '%s' for fin parameter" % type(fin))
        if isinstance(labels, str):
            self.labels = [labels for _ in range(len(self.files))]
        elif isinstance(labels, list):
            self.labels = labels
        else:
            raise Exception("Unsupported data-type '%s' for labels parameter" % type(labels))
        self.file_label_dict = dict(zip(self.files, self.labels))

        self.reader = {} # global reader will handle all files readers
        self.reader_counter = {} # reader counter keeps track of nevts read by readers

        if self.verbose:
            print(timestamp('Generator: {}'.format(self)))
            print("model parameters: {}".format(json.dumps(params)))

        self.start_idx = 0
        self.chunk_size = chunk_size
        self.stop_idx = chunk_size
        self.batch_size = batch_size

        # loop over files and create individual readers for them, then put them in a global reader
        for fname, label in self.file_label_dict.items():
            if self.dtype == 'json' or file_type(fname) == 'json':
                reader = JsonReader(fname, label, chunk_size=chunk_size, nevts=self.evts,
                        preproc=self.preproc, verbose=self.verbose)
            elif self.dtype == 'csv' or file_type(fname, label) == 'csv':
                reader = CsvReader(fname, label, chunk_size=chunk_size, nevts=self.evts,
                        preproc=self.preproc, verbose=self.verbose)
            elif self.dtype == 'avro' or file_type(fname, label) == 'avro':
                reader = AvroReader(fname, label, chunk_size=chunk_size, nevts=self.evts,
                        preproc=self.preproc, verbose=self.verbose)
            elif self.dtype == 'parquet' or file_type(fname, label) == 'parquet':
                reader = ParquetReader(fname, label, chunk_size=chunk_size, nevts=self.evts,
                        preproc=self.preproc, verbose=self.verbose)
            self.reader[fname] = reader
            self.reader_counter[fname] = 0

        self.current_file = self.files[0]

        print("init MetaDataGenerator in {} sec".format(time.time()-time0))
        print("available readers")
        for fname, reader in self.reader.items():
            print("{} {}".format(fname, reader))

    @property
    def nevts(self):
        "Return number of events of current file"
        return self.evts if self.evts != -1 else self.reader[self.current_file].nrows
         
    def __len__(self):
        "Return total number of batches this generator can deliver"
        return int(np.floor(self.nevts / self.batch_size))

    def next(self):
        "Return next batch of events"
        msg = "\nread chunk [{}:{}] from {} label {}".format(self.start_idx, self.stop_idx, self.current_file, self.file_label_dict[self.current_file])
        gen = self.read_data(self.start_idx, self.stop_idx, verbose=self.verbose)
        # advance start and stop indecies
        self.start_idx = self.stop_idx
        self.stop_idx = self.start_idx+self.chunk_size
        if self.nevts != -1 and \
           (self.start_idx > self.nevts or \
           (self.reader[self.current_file].nrows and self.start_idx > self.reader[self.current_file].nrows)):
            # we reached the limit of the reader
            self.start_idx = 0
            self.stop_idx = self.chunk_size
            raise StopIteration
        if self.verbose:
            print(msg)
        data = []
        labels = []
        for xdf, ldf in gen:
            data.append(xdf)
            labels.append(ldf)
        if not data:
            raise StopIteration
        data = np.array(data)
        # TODO: check if labels are integers or strings, if later we need to do something
        # e.g. to convert strings to categorical, but what to do if we don't have
        # full set of strings
        labels = np.array(labels)
        if self.verbose:
            print("return shapes: data=%s labels=%s" % (np.shape(data), np.shape(labels)))
        return data, labels

    def __iter__(self):
        "Provide iterator capabilities to the class"
        return self

    def __next__(self):
        "Provide generator capabilities to the class"
        return self.next()

    def read_data(self, start=0, stop=100, verbose=0):
        "Helper function to read data via reader"
        # if we exceed number of events in a file we discard it
        if self.nevts < self.reader_counter[self.current_file]:
            if self.verbose:
                msg = "# discard {} since we read {} out of {} events"\
                        .format(self.current_file, \
                        self.reader_counter[self.current_file], self.nevts)
                print(msg)
            self.files.remove(self.current_file)
            if len(self.files):
                self.current_file = self.files[0]
            else:
                print("# no more files to read from")
                raise StopIteration
        if self.shuffle:
            idx = random.randint(0, len(self.files)-1)
            self.current_file = self.files[idx]
        current_file = self.current_file
        reader = self.reader[current_file]
        for data in reader.next():
            yield data
        if stop == -1:
            read_evts = reader.nrows
        else:
            read_evts = stop-start
        # update how many events we read from current file
        self.reader_counter[self.current_file] += read_evts
        if self.verbose:
            nevts = self.reader_counter[self.current_file]
            msg = "\ntotal read {} evts from {}".format(nevts, current_file)
            print(msg)

class RootDataGenerator(object):
    """
    RootDataGenerator class provides interface to read HEP ROOT files.
    """
    def __init__(self, fin, labels, params=None, specs=None):
        "Initialization function for Data Generator"
        time0 = time.time()
        if not params:
            params = {}
        # parse given parameters
        nan = params.get('nan', np.nan)
        batch_size = params.get('batch_size', 256)
        verbose = params.get('verbose', 0)
        branch = params.get('branch', 'Events')
        branches = params.get('selected_branches', [])
        chunk_size = params.get('chunk_size', 1000)
        exclude_branches = params.get('exclude_branches', [])
        redirector = params.get('redirector', 'root://cms-xrd-global.cern.ch')
        self.evts = params.get('nevts', -1)
        self.shuffle = params.get('shuffle', False)

        # convert input fin parameter into file list if necessary
        if isinstance(fin, str):
            self.files = [fin]
        elif isinstance(fin, list):
            self.files = fin
        else:
            raise Exception("Unsupported data-type '%s' for fin parameter" % type(fin))
        if isinstance(labels, str):
            self.labels = [labels]
        elif isinstance(labels, list):
            self.labels = labels
        else:
            raise Exception("Unsupported data-type '%s' for labels parameter" % type(labels))
        self.file_label_dict = dict(zip(self.files, self.labels))

        self.reader = {} # global reader will handle all files readers
        self.reader_counter = {} # reader counter keeps track of nevts read by readers

        if verbose:
            print(timestamp('DataGenerator: {}'.format(self)))
            print("model parameters: {}".format(json.dumps(params)))

        if exclude_branches and not isinstance(exclude_branches, list):
            if os.path.isfile(exclude_branches):
                exclude_branches = \
                        [r.replace('\n', '') for r in open(exclude_branches).readlines()]
            else:
                exclude_branches = exclude_branches.split(',')
            if verbose:
                print("exclude branches", exclude_branches)

        self.start_idx = 0
        self.chunk_size = chunk_size
        self.stop_idx = chunk_size
        self.batch_size = batch_size
        self.verbose = verbose

        # loop over files and create individual readers for them, then put them in a global reader
        for fname in self.files:
            # if no specs is given try to read them from local area
            fbase = fname.split('/')[-1].replace('.root', '')
            sname = 'specs-{}.json'.format(fbase)
            if not specs:
                if os.path.isfile(sname):
                    if verbose:
                        print("loading specs {}".format(sname))
                    specs = json.load(open(sname))

            reader = RootDataReader(fname, branch=branch, selected_branches=branches,
                exclude_branches=exclude_branches, nan=nan,
                chunk_size=chunk_size, nevts=0, specs=specs,
                redirector=redirector, verbose=verbose)

            if not os.path.isfile(sname):
                if verbose:
                    print("writing specs {}".format(sname))
                reader.write_specs(sname)

            if not specs:
                reader.load_specs(sname)

            self.reader[fname] = reader
            self.reader_counter[fname] = 0

        self.current_file = self.files[0]

        print("init RootDataGenerator in {} sec".format(time.time()-time0))

    @property
    def nevts(self):
        "Return number of events of current file"
        return self.evts if self.evts != -1 else self.reader[self.current_file].nrows
         
    def __len__(self):
        "Return total number of batches this generator can deliver"
        return int(np.floor(self.nevts / self.batch_size))

    def next(self):
        "Return next batch of events in form of data and mask vectors"
        msg = "\nread chunk [{}:{}] from {} label {}".format(self.start_idx, self.stop_idx, self.current_file, self.file_label_dict[self.current_file])
        gen = self.read_data(self.start_idx, self.stop_idx, verbose=self.verbose)
        # advance start and stop indecies
        self.start_idx = self.stop_idx
        self.stop_idx = self.start_idx+self.chunk_size
        if self.start_idx > self.nevts or self.start_idx > self.reader[self.current_file].nrows:
            # we reached the limit of the reader
            self.start_idx = 0
            self.stop_idx = self.chunk_size
            raise StopIteration
        if self.verbose:
            print(msg)
        data = []
        mask = []
        for (xdf, mdf) in gen:
            data.append(xdf)
            mask.append(mdf)
        label = self.file_label_dict[self.current_file]
        labels = np.full(shape=len(data), fill_value=label, dtype=np.int)
        return np.array(data), np.array(mask), labels

    def __iter__(self):
        "Provide iterator capabilities to the class"
        return self

    def __next__(self):
        "Provide generator capabilities to the class"
        return self.next()

    def read_data(self, start=0, stop=100, verbose=0):
        "Helper function to read ROOT data via uproot reader"
        # if we exceed number of events in a file we discard it
        if self.nevts < self.reader_counter[self.current_file]:
            if self.verbose:
                msg = "# discard {} since we read {} out of {} events"\
                        .format(self.current_file, \
                        self.reader_counter[self.current_file], self.nevts)
                print(msg)
            self.files.remove(self.current_file)
            if len(self.files):
                self.current_file = self.files[0]
            else:
                print("# no more files to read from")
                raise StopIteration
        if self.shuffle:
            idx = random.randint(0, len(self.files)-1)
            self.current_file = self.files[idx]
        current_file = self.current_file
        reader = self.reader[current_file]
        if stop == -1:
            for _ in range(reader.nrows):
                xdf, mask = reader.next()
                yield (xdf, mask)
            read_evts = reader.nrows
        else:
            for _ in range(start, stop):
                xdf, mask = reader.next()
                yield (xdf, mask)
                read_evts = stop-start
        # update how many events we read from current file
        self.reader_counter[self.current_file] += read_evts
        if self.verbose:
            nevts = self.reader_counter[self.current_file]
            msg = "\ntotal read {} evts from {}".format(nevts, current_file)
            print(msg)

