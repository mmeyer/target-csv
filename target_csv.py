#!/usr/bin/env python3

import argparse
import collections
import csv
import http.client
import io
import json
import os
import sys
import threading
import urllib
from datetime import datetime

import pkg_resources
import singer
from jsonschema.validators import Draft4Validator

logger = singer.get_logger()

def emit_state(state):
    if state is not None:
        line = json.dumps(state)
        logger.debug('Emitting state {}'.format(line))
        sys.stdout.write("{}\n".format(line))
        sys.stdout.flush()

def flatten(d, parent_key='', sep='__'):
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, collections.MutableMapping):
            items.extend(flatten(v, new_key, sep=sep).items())
        else:
            items.append((new_key, str(v) if type(v) is list else v))
    return dict(items)
        
def persist_messages(delimiter, quotechar, messages, destination_path, timestamp_in_filename, append_to_export):
    state = None
    schemas = {}
    key_properties = {}
    headers = {}
    validators = {}
    
    now = datetime.now().strftime('%Y%m%dT%H%M%S')

    counter=0

    for message in messages:
        try:
            o = singer.parse_message(message).asdict()
        except json.decoder.JSONDecodeError:
            logger.error("Unable to parse:\n{}".format(message))
            raise
        message_type = o['type']
        if message_type == 'RECORD':
            counter = counter + 1
            if o['stream'] not in schemas:
                raise Exception("A record for stream {}"
                                "was encountered before a corresponding schema".format(o['stream']))

            validators[o['stream']].validate(o['record'])
            if timestamp_in_filename is True:
                filename = o['stream'] + '-' + now + '.csv'
            if timestamp_in_filename is False:
               filename = o['stream'] + '.csv'
            filename = os.path.expanduser(os.path.join(destination_path, filename))
            file_is_empty = (not os.path.isfile(filename)) or os.stat(filename).st_size == 0

            if append_to_export is False and file_is_empty is False and counter==1:
                os.remove(filename)
                file_is_empty = True

            flattened_record = flatten(o['record'])

            if o['stream'] not in headers and not file_is_empty:
                    with open(filename, 'r') as csvfile:
                        reader = csv.reader(csvfile,
                                            delimiter=delimiter,
                                            quotechar=quotechar)
                        first_line = next(reader)
                        headers[o['stream']] = first_line if first_line else flattened_record.keys()
            else:
                headers[o['stream']] = flattened_record.keys()

            with open(filename, 'a') as csvfile:
                writer = csv.DictWriter(csvfile,
                                        headers[o['stream']],
                                        extrasaction='ignore',
                                        delimiter=delimiter,
                                        quotechar=quotechar)
                if file_is_empty:
                    writer.writeheader()

                writer.writerow(flattened_record)

            state = None
        elif message_type == 'STATE':
            logger.debug('Setting state to {}'.format(o['value']))
            state = o['value']
        elif message_type == 'SCHEMA':
            stream = o['stream']
            schemas[stream] = o['schema']
            validators[stream] = Draft4Validator(o['schema'])
            key_properties[stream] = o['key_properties']
        else:
            logger.warning("Unknown message type {} in message {}"
                            .format(o['type'], o))

    return state


def send_usage_stats():
    try:
        version = pkg_resources.get_distribution('target-csv').version
        conn = http.client.HTTPConnection('collector.singer.io', timeout=10)
        conn.connect()
        params = {
            'e': 'se',
            'aid': 'singer',
            'se_ca': 'target-csv',
            'se_ac': 'open',
            'se_la': version,
        }
        conn.request('GET', '/i?' + urllib.parse.urlencode(params))
        response = conn.getresponse()
        conn.close()
    except:
        logger.debug('Collection request failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', help='Config file')
    args = parser.parse_args()

    if args.config:
        with open(args.config) as input_json:
            config = json.load(input_json)
    else:
        config = {}

    if not config.get('disable_collection', False):
        logger.info('Sending version information to singer.io. ' +
                    'To disable sending anonymous usage data, set ' +
                    'the config parameter "disable_collection" to true')
        threading.Thread(target=send_usage_stats).start()

    input_messages = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
    state = persist_messages(config.get('delimiter', ','),
                             config.get('quotechar', '"'),
                             input_messages,
                             config.get('destination_path', ''),
                             config.get('timestamp_in_filename', True),
                             config.get('append_to_export', True)),

    emit_state(state)
    logger.debug("Exiting normally")


if __name__ == '__main__':
    main()
