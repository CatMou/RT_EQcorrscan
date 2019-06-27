"""
Plotting for real-time seismic data.

:copyright:
    Calum Chamberlain

:license:
    GNU Lesser General Public License, Version 3
    (https://www.gnu.org/copyleft/lesser.html)
"""
import numpy as np
import logging
import threading
import datetime as dt

from pyproj import Proj, transform

from bokeh.plotting import figure
from bokeh.models import ColumnDataSource, HoverTool, Legend, WMTSTileSource
from bokeh.models.glyphs import MultiLine
from bokeh.models.formatters import DatetimeTickFormatter
from bokeh.layouts import gridplot, column
from bokeh.server.server import Server
from bokeh.application import Application
from bokeh.application.handlers.function import FunctionHandler

from functools import partial

Logger = logging.getLogger(__name__)


class EQcorrscanPlot:
    """
    Streaming bokeh plotting of waveforms.

    :type rt_client:
    :param rt_client: The real-time streaming client in use.
    :type plot_length: float
    :param plot_length: Plot length in seconds
    :type tribe: `eqcorrscan.core.match_filter.Tribe`
    :param tribe: Tribe of templates used in real-time detection
    :type inventory: :class:`obspy.core.station.Inventory`
    :param inventory: Inventory of stations used - will be plotted on the map.
    :type detections: list
    :param detections: List of `eqcorrscan.core.match_filter.Detection`
    :type update_interval: int
    :param update_interval: Update frequency of plot in ms
    """
    def __init__(self, rt_client, plot_length, tribe, inventory,
                 detections, update_interval=100, plot_height=800,
                 plot_width=1500):
        channels = [tr.id for tr in rt_client.buffer]
        self.channels = sorted(channels)
        self.plot_length = plot_length
        self.tribe = tribe
        self.inventory = inventory
        self.detections = detections

        self.hover = HoverTool(
            tooltips=[
                ("UTCDateTime", "@time{%m/%d %H:%M:%S}"),
                ("Amplitude", "@data")],
            formatters={'time': 'datetime'},
            mode='vline')
        self.map_hover = HoverTool(
            tooltips=[
                ("Latitude", "@lats"),
                ("Longitude", "@lons"),
                ("ID", "@id")])
        self.tools = "pan,wheel_zoom,reset"
        self.plot_options = {
            "plot_width": int(2 * (plot_width / 3)),
            "plot_height": int((plot_height - 20) / len(channels)),
            "tools": [self.hover, self.tools], "x_axis_type": "datetime"}
        self.map_options = {
            "plot_width": int(plot_width / 3), "plot_height": plot_height,
            "tools": [self.map_hover, self.tools]}
        self.updateValue = True
        Logger.info("Initializing plotter")
        make_doc = partial(
            define_plot, rt_client=rt_client, channels=channels,
            tribe=self.tribe, inventory=self.inventory,
            detections=self.detections, map_options=self.map_options,
            plot_options=self.plot_options, plot_length=self.plot_length,
            update_interval=update_interval)

        apps = {'/RT_EQcorrscan': Application(FunctionHandler(make_doc))}

        self.server = Server(apps)
        self.server.start()
        Logger.info("Plotting started")
        self.threads = []
    
    def background_run(self):
        plotting_thread = threading.Thread(
            target=self._bg_run, name="PlottingThread")
        plotting_thread.daemon = True
        plotting_thread.start()
        self.threads.append(plotting_thread)
        Logger.info("Started plotting")

    def _bg_run(self):
        print('Opening Bokeh application on http://localhost:5006/')
        self.server.io_loop.add_callback(self.server.show, "/")
        self.server.io_loop.start()

    def background_stop(self):
        self.server.io_loop.stop()
        for thread in self.threads:
            thread.join()


def define_plot(doc, rt_client, channels, tribe, inventory,
                detections, map_options, plot_options, plot_length,
                update_interval, data_color="grey", lowcut=1.0, highcut=20.0):
    """ Set up the plot. """
    # Set up the data source
    stream = rt_client.get_stream().copy().detrend()
    if lowcut and highcut:
        stream.filter("bandpass", freqmin=lowcut, freqmax=highcut)
        title = "Streaming data: {0}-{1} Hz bandpass".format(lowcut, highcut)
    elif lowcut:
        stream.filter("highpass", lowcut)
        title = "Streaming data: {0} Hz highpass".format(lowcut)
    elif highcut:
        stream.filter("lowpass", highcut)
        title = "Streaming data: {0} Hz lowpass".format(highcut)
    else:
        title = "Raw streaming data"

    template_lats, template_lons, template_alphas, template_ids = (
        [], [], [], [])
    for template in tribe:
        try:
            origin = (template.event.preferred_origin() or
                      template.event.origins[0])
        except IndexError:
            continue
        template_lats.append(origin.latitude)
        template_lons.append(origin.longitude)
        template_alphas.append(0)
        template_ids.append(template.event.resource_id.id.split("/")[-1])

    station_lats, station_lons, station_ids = ([], [], [])
    for network in inventory:
        for station in network:
            station_lats.append(station.latitude)
            station_lons.append(station.longitude)
            station_ids.append(station.code)

    # Get plot bounds in web mercator
    wgs_84 = Proj(init='epsg:4326')
    wm = Proj(init='epsg:3857')
    try:
        min_lat, min_lon, max_lat, max_lon = (
            min(template_lats + station_lats),
            min(template_lons + station_lons),
            max(template_lats + station_lats),
            max(template_lons + station_lons))
    except ValueError as e:
        Logger.error(e)
        Logger.info("Setting map bounds to NZ")
        min_lat, min_lon, max_lat, max_lon = (-47., 165., -34., 179.9)
    bottom_left = transform(wgs_84, wm, min_lon, min_lat)
    top_right = transform(wgs_84, wm, max_lon, max_lat)
    map_x_range = (bottom_left[0], top_right[0])
    map_y_range = (bottom_left[1], top_right[1])

    template_x, template_y = ([], [])
    for lon, lat in zip(template_lons, template_lats):
        _x, _y = transform(wgs_84, wm, lon, lat)
        template_x.append(_x)
        template_y.append(_y)

    station_x, station_y = ([], [])
    for lon, lat in zip(station_lons, station_lats):
        _x, _y = transform(wgs_84, wm, lon, lat)
        station_x.append(_x)
        station_y.append(_y)

    template_source = ColumnDataSource({
        'y': template_y, 'x': template_x,
        'lats': template_lats, 'lons': template_lons,
        'template_alphas': template_alphas, 'id': template_ids})
    station_source = ColumnDataSource({
        'y': station_y, 'x': station_x,
        'lats': station_lats, 'lons': station_lons, 'id': station_ids})

    trace_sources = {}
    # Allocate empty arrays
    for channel in channels:
        tr = stream.select(id=channel)[0]
        times = np.arange(
            tr.stats.starttime.datetime,
            (tr.stats.endtime + tr.stats.delta).datetime,
            step=dt.timedelta(seconds=tr.stats.delta))
        data = tr.data
        trace_sources.update(
            {channel: ColumnDataSource({'time': times, 'data': data})})

    # Set up the map to go on the left side
    map_plot = figure(
        title="Template map", x_range=map_x_range, y_range=map_y_range,
        x_axis_type="mercator", y_axis_type="mercator", **map_options)
    url = 'http://a.basemaps.cartocdn.com/rastertiles/voyager/{Z}/{X}/{Y}.png'
    attribution = "Tiles by Carto, under CC BY 3.0. Data by OSM, under ODbL"
    map_plot.add_tile(WMTSTileSource(url=url, attribution=attribution))
    map_plot.circle(
        x="x", y="y", source=template_source, color="firebrick",
        fill_alpha="template_alphas", size=10)
    map_plot.triangle(
        x="x", y="y", size=10, source=station_source, color="blue", alpha=1.0)

    # Set up the trace plots
    trace_plots = []
    now = dt.datetime.utcnow()
    p1 = figure(
        y_axis_location="right", title=title,
        x_range=[now - dt.timedelta(seconds=plot_length), now],
        plot_height=int(plot_options["plot_height"] * 1.2),
        **{key: value for key, value in plot_options.items()
           if key != "plot_height"})
    p1.yaxis.axis_label = None
    p1.xaxis.axis_label = None
    p1.min_border_bottom = 0
    p1.min_border_top = 0
    if len(channels) != 1:
        p1.xaxis.major_label_text_font_size = '0pt'
    p1_line = p1.line(
        x="time", y='data', source=trace_sources[channels[0]],
        color=data_color, line_width=1)
    legend = Legend(items=[(channels[0], [p1_line])])
    p1.add_layout(legend, 'right')

    datetick_formatter = DatetimeTickFormatter(
        days=["%m/%d"], months=["%m/%d"],
        hours=["%m/%d %H:%M:%S"], minutes=["%m/%d %H:%M:%S"],
        seconds=["%m/%d %H:%M:%S"], hourmin=["%m/%d %H:%M:%S"],
        minsec=["%m/%d %H:%M:%S"])
    p1.xaxis.formatter = datetick_formatter

    # Add detection lines
    detection_source = _get_pick_times(
        detections, channels[0], datastream={})
    detection_source.update(
        {"pick_values": [[
            int(min(stream.select(id=channels[0])[0].data) * .9),
            int(max(stream.select(id=channels[0])[0].data) * .9)]
            for _ in detection_source['picks']]})
    detection_sources = {channels[0]: ColumnDataSource(detection_source)}
    detection_lines = MultiLine(
        xs="picks", ys="pick_values", line_color="red", line_dash="dashed",
        line_width=1)
    p1.add_glyph(detection_sources[channels[0]], detection_lines)

    trace_plots.append(p1)

    if len(channels) > 1:
        for i, channel in enumerate(channels[1:]):
            p = figure(
                x_range=p1.x_range,
                y_axis_location="right", **plot_options)
            p.yaxis.axis_label = None
            p.xaxis.axis_label = None
            p.min_border_bottom = 0
            # p.min_border_top = 0
            p_line = p.line(
                x="time", y="data", source=trace_sources[channel],
                color=data_color, line_width=1)
            legend = Legend(items=[(channel, [p_line])])
            p.add_layout(legend, 'right')
            p.xaxis.formatter = datetick_formatter

            # Add detection lines
            detection_source = _get_pick_times(
                detections, channel, datastream=detection_sources)
            detection_source.update(
                {"pick_values": [[
                    int(min(stream.select(id=channel)[0].data) * .9),
                    int(max(stream.select(id=channel)[0].data) * .9)]
                    for _ in detection_source['picks']]})
            detection_sources.update({
                channel: ColumnDataSource(detection_source)})
            detection_lines = MultiLine(
                xs="picks", ys="pick_values", line_color="red",
                line_dash="dashed", line_width=1)
            p.add_glyph(detection_sources[channel], detection_lines)

            trace_plots.append(p)
            if i != len(channels) - 2:
                p.xaxis.major_label_text_font_size = '0pt'
    plots = gridplot([[map_plot, column(trace_plots)]])

    previous_timestamps = {
        channel: stream.select(id=channel)[0].stats.endtime
        for channel in channels}
    
    def update():
        Logger.debug("Plot updating")
        stream = rt_client.get_stream().copy().detrend()
        if lowcut and highcut:
            stream.filter("bandpass", freqmin=lowcut, freqmax=highcut)
        elif lowcut:
            stream.filter("highpass", lowcut)
        elif highcut:
            stream.filter("lowpass", highcut)
        for i, channel in enumerate(channels):
            try:
                tr = stream.select(id=channel)[0]
            except IndexError:
                Logger.debug("No channel for {0}".format(channel))
                continue
            new_samples = int(tr.stats.sampling_rate * (
                    previous_timestamps[channel] - tr.stats.endtime))
            if new_samples == 0:
                Logger.debug("No new data for {0}".format(channel))
                continue
            new_times = np.arange(
                previous_timestamps[channel],
                (tr.stats.endtime + tr.stats.delta).datetime,
                step=dt.timedelta(seconds=tr.stats.delta))
            _new_data = tr.slice(
                starttime=previous_timestamps[channel]).data
            new_data = {'time': new_times[1:], 'data': _new_data[1:]}
            trace_sources[channel].stream(
                new_data=new_data,
                rollover=int(plot_length * tr.stats.sampling_rate))
            new_picks = _get_pick_times(
                detections, channel, datastream=detection_sources)
            new_picks.update({
                'pick_values': [
                    [int(trace_plots[i].y_range.start * .9),
                     int(trace_plots[i].y_range.end * .9)]
                    for _ in new_picks['picks']]})
            detection_sources[channel].stream(
                new_data=new_picks,
                rollover=int(plot_length * tr.stats.sampling_rate))
            previous_timestamps.update({channel: tr.stats.endtime})
            Logger.debug("New data plotted for {0}".format(channel))
        now = dt.datetime.utcnow()
        trace_plots[0].x_range.start = now - dt.timedelta(seconds=plot_length)
        trace_plots[0].x_range.end = now
        _update_template_alphas(
            detections, tribe, decay=plot_length / 10, now=now,
            datastream=template_source)

    doc.add_periodic_callback(update, update_interval)
    doc.title = "EQcorrscan Real-time plotter"
    doc.add_root(plots)


def _update_template_alphas(detections, tribe, decay, now, datastream):
    """
    Update the template location datastream.

    :type detections: list of `eqcorrscan.core.match_filter.Detection`
    :param detections: Detections to use to update the datastream
    :type tribe: `eqcorrscan.core.match_filter.Tribe`
    :param tribe: Templates used
    :type decay: float
    :param decay: Colour decay length in seconds
    :type now: `datetime.datetime`
    :param now: Reference time-stamp
    :type datastream: `bokeh.models.DataStream`
    :param datastream: Data stream to update
    """
    wgs_84 = Proj(init='epsg:4326')
    wm = Proj(init='epsg:3857')
    template_lats, template_lons, template_alphas, template_ids = (
        [], [], [], [])
    template_x, template_y = ([], [])
    for template in tribe:
        try:
            origin = (template.event.preferred_origin() or
                      template.event.origins[0])
        except IndexError:
            continue
        template_lats.append(origin.latitude)
        template_lons.append(origin.longitude)

        template_ids.append(template.event.resource_id.id.split("/")[-1])
        _x, _y = transform(wgs_84, wm, origin.longitude, origin.latitude)
        template_x.append(_x)
        template_y.append(_y)
        template_detections = [
            d for d in detections if d.template_name == template.name]
        if len(template_detections) == 0:
            template_alphas.append(0)
        else:
            detect_time = min([d.detect_time for d in template_detections])
            offset = (now - detect_time.datetime).total_seconds()
            alpha = 1. - (offset / decay)
            Logger.debug('Updating alpha to {0:.4f}'.format(alpha))
            template_alphas.append(alpha)
    datastream.data = {'y': template_y, 'x': template_x, 'lats': template_lats,
                       'lons': template_lons,
                       'template_alphas': template_alphas, 'id': template_ids}
    return


def _get_pick_times(detections, seed_id, datastream):
    """
    Get new pick times from catalog for a given channel.

    :type detections: list of `eqcorrscan.core.match_filter.Detection
    :param detections: List of detections
    :type seed_id: str
    :param seed_id: The full Seed-id (net.sta.loc.chan) for extract picks for
    :type datastream: dict
    :param datastream:
        Dictionary keyed by seed-id containing the DataStreams used for
        plotting the picks. Will compare against this to find new picks.

    :rtype: dict
    :return: Dictionary with one key ("picks") of the pick-times.
    """
    picks = []
    for detection in detections:
        try:
            pick = [p for p in detection.event.picks
                    if p.waveform_id.get_seed_string() == seed_id][0]
        except IndexError:
            pick = None
            pass
        if pick:
            old_picks = datastream.get(seed_id, None)
            if old_picks is None:
                old_picks = []
            else:
                old_picks = old_picks.data["picks"]
            if [pick.time.datetime, pick.time.datetime] not in old_picks:
                Logger.debug("Plotting new pick on {0} at {1}".format(
                    seed_id, pick.time))
                picks.append([pick.time.datetime, pick.time.datetime])
    return {"picks": picks}


if __name__ == "__main__":
    import doctest

    doctest.testmod()