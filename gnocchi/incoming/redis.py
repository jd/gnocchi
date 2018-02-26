# -*- encoding: utf-8 -*-
#
# Copyright © 2017-2018 Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
import contextlib

import six

from gnocchi.common import redis
from gnocchi import incoming


class RedisStorage(incoming.IncomingDriver):

    _SCRIPTS = {
        "process_measure_for_sack": """
local results = {}
local metric_id_extractor = "[^%s]*%s([^%s]*)"
local metric_with_measures = redis.call("KEYS", KEYS[1] .. "%s*")
for i, sack_metric in ipairs(metric_with_measures) do
    local llen = redis.call("LLEN", sack_metric)
    local metric_id = sack_metric:gmatch(metric_id_extractor)()
    -- lrange is inclusive on both ends, decrease to grab exactly n items
    if llen > 0 then llen = llen - 1 end
    results[metric_id] = {
        llen, table.concat(redis.call("LRANGE", sack_metric, 0, llen), "")
    }
end
return results
""" % (redis.SEP, redis.SEP, redis.SEP redis.SEP),
    }

    def __init__(self, conf, greedy=True):
        super(RedisStorage, self).__init__(conf)
        self._client, self._scripts = redis.get_client(conf, self._SCRIPTS)
        self.greedy = greedy

    def __str__(self):
        return "%s: %s" % (self.__class__.__name__, self._client)

    def _get_storage_sacks(self):
        return self._client.hget(self.CFG_PREFIX, self.CFG_SACKS)

    def set_storage_settings(self, num_sacks):
        self._client.hset(self.CFG_PREFIX, self.CFG_SACKS, num_sacks)

    @staticmethod
    def remove_sacks():
        # NOTE(gordc): redis doesn't maintain keys with empty values
        pass

    def _build_measure_path_with_sack(self, metric_id, sack_name):
        return redis.SEP.join([sack_name.encode(), str(metric_id).encode()])

    def _build_measure_path(self, metric_id):
        return self._build_measure_path_with_sack(
            metric_id, str(self.sack_for_metric(metric_id)))

    def add_measures_batch(self, metrics_and_measures):
        notified_sacks = set()
        pipe = self._client.pipeline(transaction=False)
        for metric_id, measures in six.iteritems(metrics_and_measures):
            sack_name = str(self.sack_for_metric(metric_id))
            path = self._build_measure_path_with_sack(metric_id, sack_name)
            pipe.rpush(path, self._encode_measures(measures))
            if self.greedy and sack_name not in notified_sacks:
                # value has no meaning, we just use this for notification
                pipe.setnx(sack_name, 1)
                notified_sacks.add(sack_name)
        pipe.execute()

    def _build_report(self, details):
        report_vars = {'measures': 0, 'metric_details': {}}

        def update_report(results, m_list):
            report_vars['measures'] += sum(results)
            if details:
                report_vars['metric_details'].update(
                    dict(six.moves.zip(m_list, results)))

        match = redis.SEP.join([self._get_sack_name("*").encode(), b"*"])
        metrics = 0
        m_list = []
        pipe = self._client.pipeline()
        for key in self._client.scan_iter(match=match, count=1000):
            metrics += 1
            pipe.llen(key)
            if details:
                m_list.append(key.split(redis.SEP)[1].decode("utf8"))
            # group 100 commands/call
            if metrics % 100 == 0:
                results = pipe.execute()
                update_report(results, m_list)
                m_list = []
                pipe = self._client.pipeline()
        else:
            results = pipe.execute()
            update_report(results, m_list)
        return (metrics, report_vars['measures'],
                report_vars['metric_details'] if details else None)

    def list_metric_with_measures_to_process(self, sack):
        match = redis.SEP.join([str(sack).encode(), b"*"])
        keys = self._client.scan_iter(match=match, count=1000)
        return set([k.split(redis.SEP)[1].decode("utf8") for k in keys])

    def delete_unprocessed_measures_for_metric(self, metric_id):
        self._client.delete(self._build_measure_path(metric_id))

    def has_unprocessed(self, metric_id):
        return bool(self._client.exists(self._build_measure_path(metric_id)))

    @contextlib.contextmanager
    def process_measure_for_sack(self, sack):
        results = self._scripts['process_measure_for_sack'](keys=[str(sack)])

        for metric_id, (item_len, data) in results:
            measures[metric_id] = self._unserialize_measures(metric_id, data)

        yield measures

        pipe = self._client.pipeline()
        for metric_id, (item_len, data) in results:
            key = self._build_measure_path(metric_id)
            # ltrim is inclusive, bump 1 to remove up to and including nth item
            pipe.ltrim(key, item_len + 1, -1)
        pipe.execute()

    def iter_on_sacks_to_process(self):
        self._client.config_set("notify-keyspace-events", "K$")
        p = self._client.pubsub()
        db = self._client.connection_pool.connection_kwargs['db']
        keyspace = b"__keyspace@" + str(db).encode() + b"__:"
        pattern = keyspace + self._get_sack_name("*").encode()
        p.psubscribe(pattern)
        for message in p.listen():
            if message['type'] == 'pmessage' and message['pattern'] == pattern:
                # FIXME(jd) This is awful, we need a better way to extract this
                # Format is defined by _get_sack_name: incoming128-17
                yield self._make_sack(int(message['channel'].split(b"-")[-1]))

    def finish_sack_processing(self, sack):
        # Delete the sack key which handles no data but is used to get a SET
        # notification in iter_on_sacks_to_process
        self._client.delete(str(sack))
