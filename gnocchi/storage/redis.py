# -*- encoding: utf-8 -*-
#
# Copyright © 2017-2018 Red Hat
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
import six

from gnocchi import carbonara
from gnocchi.common import redis
from gnocchi import storage
from gnocchi import utils


class RedisStorage(storage.StorageDriver):
    WRITE_FULL = True

    STORAGE_PREFIX = b"timeseries"
    FIELD_SEP = '_'
    FIELD_SEP_B = b'_'

    _SCRIPTS = {
        "list_split_keys": """
local metric_key = KEYS[1]
local ids = {}
local cursor = 0
local substring = "([^%s]*)%s([^%s]*)%s([^%s]*)"
repeat
    local result = redis.call("HSCAN", metric_key, cursor, "MATCH", ARGV[1])
    cursor = tonumber(result[1])
    for i, v in ipairs(result[2]) do
        -- Only return keys, not values
        if i %% 2 ~= 0 then
            local timestamp, method, granularity = v:gmatch(substring)()
            ids[#ids + 1] = {timestamp, method, granularity}
        end
    end
until cursor == 0
return ids
""" % (FIELD_SEP, FIELD_SEP, FIELD_SEP, FIELD_SEP, FIELD_SEP),
        "get_measures": """
local results = redis.call("HMGET", KEYS[1], unpack(ARGV))
local final = {}
local metric_exists = false
for i, result in ipairs(results) do
    if result == false then
        if not metric_exists and redis.call("EXISTS", KEYS[1]) == 0 then
            return {-2, false}
        else
            metric_exists = true
        end
    end
    final[#final + 1] = result
end
return {0, final}
""",
    }

    def __init__(self, conf):
        super(RedisStorage, self).__init__(conf)
        self._client, self._scripts = redis.get_client(conf, self._SCRIPTS)

    def __str__(self):
        return "%s: %s" % (self.__class__.__name__, self._client)

    def _metric_key(self, metric):
        return redis.SEP.join([self.STORAGE_PREFIX, str(metric.id).encode()])

    @staticmethod
    def _unaggregated_field(version=3):
        return 'none' + ("_v%s" % version if version else "")

    @classmethod
    def _aggregated_field_for_split(cls, aggregation, key, version=3,
                                    granularity=None):
        path = cls.FIELD_SEP.join([
            str(key), aggregation,
            str(utils.timespan_total_seconds(granularity or key.sampling))])
        return path + '_v%s' % version if version else path

    def _store_unaggregated_timeseries(self, metrics_and_data, version=3):
        pipe = self._client.pipeline(transaction=False)
        unagg_key = self._unaggregated_field(version)
        for metric, data in metrics_and_data:
            pipe.hset(self._metric_key(metric), unagg_key, data)
        pipe.execute()

    def _get_or_create_unaggregated_timeseries(self, metrics, version=3):
        pipe = self._client.pipeline(transaction=False)
        for metric in metrics:
            metric_key = self._metric_key(metric)
            unagg_key = self._unaggregated_field(version)
            # Create the metric if it was not created
            pipe.hsetnx(metric_key, unagg_key, "")
            # Get the data
            pipe.hget(metric_key, unagg_key)
        ts = {
            # Replace "" by None
            metric: data or None
            for metric, (created, data)
            in six.moves.zip(metrics, utils.grouper(pipe.execute(), 2))
        }
        return ts

    def _list_split_keys(self, metric, aggregations, version=3):
        key = self._metric_key(metric)
        pipe = self._client.pipeline(transaction=False)
        pipe.exists(key)
        for aggregation in aggregations:
            self._scripts["list_split_keys"](
                keys=[key], args=[self._aggregated_field_for_split(
                    aggregation.method, "*",
                    version, aggregation.granularity)],
                client=pipe,
            )
        results = pipe.execute()
        metric_exists_p = results.pop(0)
        if not metric_exists_p:
            raise storage.MetricDoesNotExist(metric)
        keys = {}
        for aggregation, k in six.moves.zip(aggregations, results):
            if not k:
                keys[aggregation] = set()
                continue
            timestamps, methods, granularities = list(zip(*k))
            timestamps = utils.to_timestamps(timestamps)
            granularities = map(utils.to_timespan, granularities)
            keys[aggregation] = {
                carbonara.SplitKey(timestamp,
                                   sampling=granularity)
                for timestamp, granularity
                in six.moves.zip(timestamps, granularities)
            }
        return keys

    def _delete_metric_splits(self, metric, keys, aggregation, version=3):
        metric_key = self._metric_key(metric)
        pipe = self._client.pipeline(transaction=False)
        for key in keys:
            pipe.hdel(metric_key, self._aggregated_field_for_split(
                aggregation, key, version))
        pipe.execute()

    def _store_metric_splits(self, metric, keys_and_data_and_offset,
                             aggregation, version=3):
        pipe = self._client.pipeline(transaction=False)
        metric_key = self._metric_key(metric)
        for key, data, offset in keys_and_data_and_offset:
            key = self._aggregated_field_for_split(
                aggregation.method, key, version)
            pipe.hset(metric_key, key, data)
        pipe.execute()

    def _delete_metric(self, metric):
        self._client.delete(self._metric_key(metric))

    def _get_measures(self, metric, keys_and_aggregations, version=3):
        if not keys_and_aggregations:
            return []
        fields = [
            self._aggregated_field_for_split(aggregation.method, key, version)
            for key, aggregation in keys_and_aggregations
        ]
        code, result = self._scripts['get_measures'](
            keys=[self._metric_key(metric)],
            args=fields,
        )
        if code == -2:
            raise storage.MetricDoesNotExist(metric)
        return result
