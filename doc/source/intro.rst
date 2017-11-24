Getting started
---------------
Gnocchi uses three different back-ends for storing data: one for storing new
incoming |measures| (the *incoming* driver), one for storing the |time series|
|aggregates| (the *storage* driver) and one for indexing the data (the *index*
driver). By default, the *incoming* driver is configured to use the same value
as the *storage* driver.

Incoming and storage drivers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Gnocchi can leverage different storage systems, such as:

* File (default)
* `Ceph`_ (preferred)
* `OpenStack Swift`_
* `Amazon S3`_
* `Redis`_

Depending on the size of your architecture, using the file driver and storing
your data on a disk might be enough. If you need to scale the number of server
with the file driver, you can export and share the data via NFS among all
Gnocchi processes. Ultimately, the S3, Ceph, and Swift drivers are more
scalable storage options. Ceph also offers better consistency, and hence is the
recommended driver.

.. _`OpenStack Swift`: http://docs.openstack.org/developer/swift/
.. _`Ceph`: https://ceph.com
.. _`Amazon S3`: https://aws.amazon.com/s3/
.. _`Redis`: https://redis.io

Indexer driver
~~~~~~~~~~~~~~

You also need a database to index the resources and metrics that Gnocchi will
handle. The supported drivers are:

* `PostgreSQL`_ (preferred)
* `MySQL`_ (at least version 5.6.4)

The *indexer* is responsible for storing the index of all |resources|, |archive
policies| and |metrics|, along with their definitions, types and properties.
The indexer is also responsible for linking |resources| with |metrics| and the
relationships of |resources|..

.. _PostgreSQL: http://postgresql.org
.. _MySQL: http://mysql.org

Architecture overview
---------------------

Gnocchi consists of several services: a HTTP REST API (see :doc:`rest`), an
optional statsd-compatible daemon (see :doc:`statsd`), and an asynchronous
processing daemon (named `gnocchi-metricd`). Data is received via the HTTP REST
API or statsd daemon. `gnocchi-metricd` performs operations (statistics
computing, |metric| cleanup, etc...) on the received data in the background.

.. image:: _static/architecture.svg
  :align: center
  :width: 95%
  :alt: Gnocchi architecture

.. image source: https://docs.google.com/drawings/d/1aHV86TPNFt7FlCLEjsTvV9FWoFYxXCaQOzfg7NdXVwM/edit?usp=sharing

All those services are stateless and therefore horizontally scalable. Contrary
to many time series databases, there is no limit on the number of
`gnocchi-metricd` daemons or `gnocchi-api` endpoints that you can run. If your
load starts to increase, you just need to spawn more daemons to handle the flow
of new requests. The same applies if you want to handle high-availability
scenarios: just start more Gnocchi daemons on independent servers.


Understanding aggregation
-------------------------

The way data points are aggregated is configurable on a per-metric basis, using
an archive policy.

An archive policy defines which aggregations to compute and how many aggregates
to keep. Gnocchi supports a variety of aggregation methods, such as minimum,
maximum, average, Nth percentile, standard deviation, etc. Those aggregations
are computed over a period of time (called granularity) and are kept for a
defined timespan.


Comparisons To Alternatives
---------------------------

The following table summarises feature comparison between different existing
open source time series database. More details are written below, if needed.

.. include:: comparison-table.rst

Gnocchi vs Prometheus
~~~~~~~~~~~~~~~~~~~~~
`Prometheus <https://prometheus.io/>`_ is a full-featured solution that
includes everything from polling the metrics to storing and archiving them. It
offers advanced features such as alerting.

In comparison, Gnocchi does not offer polling as it prefers to leverage
existing solutions (e.g. `collectd <http://collectd.org>`_). However, it
provides high-availability and horizontal scalablity as well as multi-tenancy.


Gnocchi vs InfluxDB
~~~~~~~~~~~~~~~~~~~

`InfluxDB <http://influxdb.org>`_ is a time series database storing metrics
into local files. It offers a variety of input protocol support and created its
own query language, InfluxQL, inspired from SQL. The HTTP API it offers is just
a way to pass InfluxQL over the wire. Horizontal scalability is only provided
in the commercial version. The data model is based on time series with labels
associated to it.

In comparison, Gnocchi offers scalability and multi-tenancy. Its data model
differs as it does not provide labels, but |resources| to attach to |metrics|.

Gnocchi vs OpenTSDB
~~~~~~~~~~~~~~~~~~~

`OpenTSDB <http://opentsdb.net/>`_ is a distributed time series database that
uses `Hadoop <http://hadoop.apache.org/>`_ and `HBase
<http://hbase.apache.org/>`_ to store its data. That makes it easy to scale
horizontally. However, its querying feature are rather simple.

In comparison, Gnocchi offers a proper query language with more features. The
usage of Hadoop might be a show-stopper for many as it's quite heavy to deploy
and operate.

Gnocchi vs Graphite
~~~~~~~~~~~~~~~~~~~

`Graphite <http://graphite.readthedocs.org/en/latest/>`_ is essentially a data
metric storage composed of flat files (Whisper), and focuses on rendering those
time series. Each time series stored is composed of points that are stored
regularly and are related to the current date and time.

In comparison, Gnocchi offers much more scalability, a better file format and
no relativity to the current time and date.

.. include:: include/term-substitution.rst
