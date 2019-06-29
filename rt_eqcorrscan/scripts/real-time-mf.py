#!/usr/bin/env python3
"""
Script to run the real-time matched filter for a given region or earthquake.
"""

import logging

from concurrent.futures import ProcessPoolExecutor

from obspy import Stream

from rt_eqcorrscan.config.config import Config
from rt_eqcorrscan.core.reactor import estimate_region, get_inventory
from rt_eqcorrscan.core.database_manager import TemplateBank
from rt_eqcorrscan.core.rt_match_filter import RealTimeTribe


Logger = logging.getLogger("real-time-mf")


def run_real_time_matched_filter(**kwargs):
    config = Config(config_file=kwargs.get("config_file", None))
    client = config.Client(config.client)

    triggering_eventid = kwargs.get("eventid", None)

    if triggering_eventid:
        triggering_event = client.get_events(
            eventid=triggering_eventid)[0]
        region = estimate_region(triggering_event)
    else:
        triggering_event = None
        region = {
            "latitude": kwargs.get("latitude", None),
            "longitude": kwargs.get("longitude", None),
            "maxradius": kwargs.get("maxradius", None)}
    bank = TemplateBank(
        config.event_path, event_format=config.event_format,
        path_structure=config.path_structure, event_ext=config.event_ext)
    Logger.info("Reading in tribe")

    with ProcessPoolExecutor(max_workers=8) as executor:
        tribe = bank.get_templates(executor=executor, **region)

    Logger.info("Read in tribe of {0} templates".format(len(tribe)))

    inventory = get_inventory(
        client, tribe, triggering_event=triggering_event,
        max_distance=1000, n_stations=10)

    used_channels = {
        "{net}.{sta}.{loc}.{chan}".format(
            net=net.code, sta=sta.code, loc=chan.location_code, chan=chan.code)
        for net in inventory for sta in net for chan in sta}

    _templates = []
    for template in tribe:
        _st = Stream()
        for tr in template.st:
            if tr.id in used_channels:
                _st += tr
        template.st = _st
        t_stations = {tr.stats.station for tr in template.st}
        if len(t_stations) >= 5:
            _templates.append(template)
    tribe.templates = _templates

    for t in tribe:
        t.process_length = config.buffer_capacity

    real_time_tribe = RealTimeTribe(
        tribe=tribe, inventory=inventory,
        server_url=config.seedlink_server_url,
        buffer_capacity=config.buffer_capacity,
        detect_interval=config.detect_interval, plot=config.plot,
        plot_length=config.plot_length)

    party = real_time_tribe.run(
        threshold=config.threshold, threshold_type=config.threshold_type,
        trig_int=config.trig_int)
    return party


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Real Time Matched Filter")
    parser.add_argument(
        "--eventid", "-e", type=str, help="Triggering event ID",
        required=False)
    parser.add_argument(
        "--latitude", type=float, help="Latitude for template-search",
        required=False)
    parser.add_argument(
        "--longitude", type=float, help="Longitude for template-search",
        required=False)
    parser.add_argument(
        "--radius", type=float, help="Radius (in degrees) for template-search",
        required=False)
    parser.add_argument(
        "--config", "-c", type=str, help="Path to configuration file",
        required=False)

    args = parser.parse_args()
    if args.eventid is not None:
        kwargs = {"eventid": args.eventid}
    elif args.latitude is not None:
        assert (args.longitude is not None,
                "Latitude, longitude and radius must all be specified")
        assert (args.radius is not None,
                "Latitude, longitude and radius must all be specified")
        kwargs = {"latitude": args.latitude, "longitude": args.longitude,
                  "maxradius": args.radius}
    else:
        raise NotImplementedError(
            "Needs either an event id or a geographic search")

    kwargs.update({"config_file": args.config})

    run_real_time_matched_filter(**kwargs)