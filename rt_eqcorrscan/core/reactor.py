"""
Overarching tool for listening to and triggering from FDSN earthquakes.

TODO: Write this script: This should:
 - Use pre-computed Tribes covering patches of the country,
 - Listen to GeoNet earthquake feed
 - If an earthquake of interest happens, load the tribe for that region and
   start the real-time matched-filter
 - Once the detection rate drops low enough, stop running it?

 - Alongside this - check whether new detections made by GeoNet need to be
   included in the database.
"""
import logging
import threading

from collections import Counter

from obspy import Inventory
from obspy.core.event import Event, Catalog
from obspy.clients.fdsn.client import FDSNNoDataException
from obspy.geodetics import locations2degrees, kilometer2degrees

from rt_eqcorrscan.core.database_manager import TemplateBank
from rt_eqcorrscan.core.rt_match_filter import RealTimeTribe
from rt_eqcorrscan.utils.event_trigger.catalog_listener import (
    CatalogListener, event_time)


Logger = logging.getLogger(__name__)


class Reactor(object):
    """
    A class to listen to a client and start-up a real-time instance.

    The real-time instance will be triggered by the listener when set
    conditions are met. Appropriate templates will be extracted from the
    template database on disk and used a Tribe for the real-time detection.

    Once triggered, the listener will keep listening, but will not trigger in
    the same region again while the real-time detector is running. The real-time
    detector has to be stopped manually.

    Parameters
    ----------
    client
        An obspy or obsplus client that supports event and station queries.
    seedlink_server_url
        The url to a seedlink server that will be used to real-time
        matched-filtering
    listener
        Listener for checking current earthquake activity
    template_database
        A template database to be used to generate tribes for real-time
        matched-filter detection.
    """
    triggered_events = []
    running_templates_ids = []  # A list of currently running templates
    max_station_distance = 1000
    n_stations = 10

    # The threads that are detecting away!
    detecting_threads = []

    def __init__(
        self,
        client,
        seedlink_server_url: str,
        listener: CatalogListener,
        trigger_func,
        template_database: TemplateBank,
        real_time_tribe_kwargs: dict,
    ):
        self.client = client
        self.seedlink_server_url = seedlink_server_url
        self.listener = listener
        self.trigger_func = trigger_func
        self.template_database = template_database
        self.real_time_tribe_kwargs = real_time_tribe_kwargs

    def run(self):
        self.listener.background_run()
        # Query the catalog in the listener every so often and check
        while True:
            working_cat = self.listener.catalog
            Logger.debug("Currently analysing a catalog of {0} events".format(
                len(working_cat)))

            trigger_events = self.trigger_func(working_cat)
            for trigger_event in trigger_events:
                if trigger_event not in self.triggered_events:
                    Logger.warning(
                        "Listener triggered by event {0}".format(
                            trigger_event))
                    self.triggered_events.append(trigger_event)
                    self.background_spin_up(trigger_event)

    def background_spin_up(self, triggering_event: Event):
        detecting_thread = threading.Thread(
            target=self.run, args=(triggering_event, ), name="DetectingThread")
        detecting_thread.daemon = True
        detecting_thread.start()
        self.detecting_threads.append(detecting_thread)
        Logger.info("Started detecting")

    def stop(self):
        for detecting_thread in self.detecting_threads:
            detecting_thread.join()
        self.listener.background_stop()

    def spin_up(self, triggering_event: Event, threshold: float,
                threshold_type: str, trig_int: float):
        """
        Run the reactors response function.
        """
        region = estimate_region(triggering_event)
        if region is None:
            return
        tribe = self.template_database.get_templates(**region)
        tribe.templates = [t for t in tribe
                           if t.name not in self.running_templates_ids]
        inventory = get_inventory(
            self.client, tribe, triggering_event=triggering_event,
            max_distance=self.max_station_distance,
            n_stations=self.n_stations)
        buffer_capacity = self.real_time_tribe_kwargs.get(
            "buffer_capacity", 600)
        detect_interval = self.real_time_tribe_kwargs.get(
            "detect_interval", 60)
        plot = self.real_time_tribe_kwargs.get("plot", False)
        plot_length = self.real_time_tribe_kwargs.get(
            "plot_length", 300)

        real_time_tribe = RealTimeTribe(
            tribe=tribe, inventory=inventory,
            server_url=self.seedlink_server_url,
            buffer_capacity=buffer_capacity,
            detect_interval=detect_interval, plot=plot,
            plot_length=plot_length)

        self.running_templates_ids.append(
            [t.name for t in real_time_tribe.templates])
        keep_detections = self.real_time_tribe_kwargs.get(
            "keep_detections", 86400)
        detect_directory = self.real_time_tribe_kwargs.get(
            "detect_directory", "detections")
        max_run_length = self.real_time_tribe_kwargs.get(
            "max_run_length", None)
        real_time_tribe.run(
            threshold=threshold, threshold_type=threshold_type,
            trig_int=trig_int, kepp_detection=keep_detections,
            detect_directory=detect_directory, max_run_length=max_run_length,
            **self.real_time_tribe_kwargs)


def get_inventory(client, tribe, triggering_event, max_distance=1000,
                  n_stations=10, duration=10, level="channel"):
    """
    Get a suitable inventory for the tribe - selects the most used, closest
    stations.
    """
    inv = Inventory(networks=[], source=None)
    try:
        origin = (
            triggering_event.preferred_origin() or triggering_event.origins[0])
    except IndexError:
        Logger.error("Triggering event has no origin")
        return inv

    for channel_str in ["EH?", "HH?"]:
        try:
            inv += client.get_stations(
                startbefore=origin.time,
                endafter=origin.time + (duration * 86400),
                channel=channel_str, latitude=origin.latitude,
                longitude=origin.longitude,
                maxradius=kilometer2degrees(max_distance),
                level=level)
        except FDSNNoDataException:
            continue
    inv_len = 0
    for net in inv:
        inv_len += len(net)
    if inv_len <= n_stations:
        return [sta.code for net in inv for sta in net]
    # Calculate distances

    station_count = Counter(
        [pick.waveform_id.station_code for template in tribe
         for pick in template.event.picks])

    sta_dist = []
    for net in inv:
        for sta in net:
            dist = locations2degrees(
                lat1=origin.latitude, long1=origin.longitude,
                lat2=sta.latitude, long2=sta.longitude)
            sta_dist.append((sta.code, dist, station_count[sta.code]))
    sta_dist.sort(key=lambda _: (-_[2], _[1]))
    inv_out = inv.select(station=sta_dist[0][0])
    for sta in sta_dist[1:n_stations]:
        inv_out += inv.select(station=sta[0])
    return inv_out


def estimate_region(event: Event, min_length: float = 50.):
    """
    Estimate the region to find templates within given a triggering event.

    Parameters
    ----------
    event
        The event that triggered this function
    min_length
        Minimum length in km for diameter of event circle around the
        triggering event
    """
    from obspy.geodetics import kilometer2degrees
    try:
        origin = event.preferred_origin() or event.origins[0]
    except IndexError:
        Logger.error("Triggering event has no origin, not using.")
        return None

    try:
        magnitude = event.preferred_magnitude() or event.magnitudes[0]
    except IndexError:
        Logger.warning("Triggering event has no magnitude, using minimum "
                       "length or {0}".format(min_length))
        magnitude = None
    if magnitude:
        length = 10 ** ((magnitude.mag - 5.08) / 1.16)  # Wells and Coppersmith
    else:
        length = min_length

    # Scale up a bit - for Darfield this gave 0.6 deg, but the aftershock
    # region is more like 1.2 deg radius
    length *= 1.25

    if length < min_length:
        length = min_length
    length = kilometer2degrees(length)
    length /= 2.
    return {
        "latitude": origin.latitude, "longitude": origin.longitude,
        "maxradius": length}


def example_trigger_func(catalog, magnitude_threshold=5.5, rate_threshold=20.,
                         rate_bin=.2):
    """
    Function to turn triggered response on.

    :type catalog: `obspy.core.event.Catalog`
    :param catalog: Catalog to look in
    :type magnitude_threshold: float
    :param magnitude_threshold: magnitude threshold for triggering a response
    :type rate_threshold: float
    :param rate_threshold: rate in events per day for triggering a response
    :type rate_bin: float
    :param rate_bin: radius in degrees to calculate rate for.

    :rtype: `obspy.core.event.Event`
    :returns: The event that forced the trigger.
    """
    trigger_events = Catalog()
    for event in catalog:
        try:
            magnitude = event.preferred_magnitude() or event.magnitudes[0]
        except IndexError:
            continue
        if magnitude.mag >= magnitude_threshold:
            trigger_events.events.append(event)
    for event in catalog:
        sub_catalog = get_nearby_events(event, catalog, radius=rate_bin)
        rate = average_rate(sub_catalog)
        if rate >= rate_threshold:
            for _event in sub_catalog:
                if _event not in trigger_events:
                    trigger_events.events.append(_event)
    if len(trigger_events) > 0:
        return trigger_events
    return []


def get_nearby_events(event, catalog, radius):
    """
    Get a catalog of events close to another event.

    :type event: `obspy.core.event.Event`
    :param event: Central event to calculate distance relative to
    :type catalog: `obspy.core.event.Catalog`
    :param catalog: Catalog to extract events from
    :type radius: float
    :param radius: Radius around `event` in km

    :rtype: `obspy.core.event.Catalog`
    :return: Catalog of events close to `event`
    """
    sub_catalog = Catalog(
        [e for e in catalog.events
         if inter_event_distance(event, e) <= radius])
    return sub_catalog


def inter_event_distance(event1, event2):
    """
    Calculate the distance (in degrees) between two events

    :rtype: float
    :return: distance in degrees between events
    """
    try:
        origin_1 = event1.preferred_origin() or event1.origins[0]
        origin_2 = event2.preferred_origin() or event2.origins[0]
    except IndexError:
        return 180.
    return locations2degrees(
        lat1=origin_1.latitude, long1=origin_1.longitude,
        lat2=origin_2.latitude, long2=origin_2.longitude)


def average_rate(catalog):
    """
    Compute mean rate of occurrence of events in catalog.

    :type catalog: `obspy.core.event.Catalog`
    :param catalog: Catalog of events

    :rtype: float
    :return: rate
    """
    if len(catalog) <= 1:
        return 0.
    event_times = [event_time(e) for e in catalog]
    rates = [event_times[i] - event_times[i - 1]
             for i in range(len(event_times) - 1)]
    return sum(rates) / len(rates)


if __name__ == "__main__":
    import doctest

    doctest.testmod()