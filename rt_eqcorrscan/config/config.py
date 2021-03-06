"""
Handle configuration of RT_EQcorrscan using a yaml file.

Author
    Calum J Chamberlain
License
    GPL v3.0

"""
import logging
import os
import sys

from yaml import load, dump
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:  # pragma: no cover
    from yaml import Loader, Dumper
from logging.handlers import RotatingFileHandler

from obspy.core.util import AttribDict


Logger = logging.getLogger(__name__)


class _ConfigAttribDict(AttribDict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def to_yaml_dict(self):
        return {
            key.replace("_", " "): value
            for key, value in self.__dict__.items()}

    def __eq__(self, other):
        if set(self.__dict__.keys()) != set(other.__dict__.keys()):
            return False
        for key in self.__dict__.keys():
            if self[key] != other[key]:
                return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)


class RTMatchFilterConfig(_ConfigAttribDict):
    """
    A holder for configuration values for real-time matched-filtering.

    Works like a dictionary and can have anything added to it.
    """
    defaults = {
        "client": "GEONET",
        "client_type": "FDSN",
        "seedlink_server_url": "link.geonet.org.nz",
        "n_stations": 10,
        "min_stations": 5,
        "max_distance": 1000.,
        "buffer_capacity": 300.,
        "detect_interval": 120.,
        "max_run_length": None,
        "minimum_rate": None,
        "plot": True,
        "threshold": .5,
        "threshold_type": "av_chan_corr",
        "trig_int": 2.0,
        "local_wave_bank": None,
        "save_waveforms": False,
        "plot_detections": False,
    }
    readonly = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_client(self):
        """ Get the client instance given the set parameters. """
        from obspy import clients

        try:
            _client_module = clients.__getattribute__(self.client_type.lower())
        except AttributeError as e:
            Logger.error(e)
            return None
        try:
            client = _client_module.Client(self.client)
        except Exception as e:
            Logger.error(e)
            return None
        return client

    def get_waveform_client(self):
        """ Get the waveform client instance given the set parameters. """
        from obspy import clients

        try:
            _client_module = clients.__getattribute__(
                self.waveform_client_type.lower())
        except AttributeError as e:
            Logger.error(e)
            return None
        try:
            client = _client_module.Client(self.waveform_client)
        except Exception as e:
            Logger.error(e)
            return None
        return client


class ReactorConfig(_ConfigAttribDict):
    """
    A holder for configuration values for the reactor.

    Works like a dictionary and can have anything added to it.
    """
    defaults = {
        "magnitude_threshold": 6.0,
        "rate_threshold": 20.0,
        "rate_radius": 0.2,
        "minimum_events_in_bin": 10,
        "catalog_lookup_kwargs": dict(),
    }
    readonly = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class PlotConfig(_ConfigAttribDict):
    """
    A holder for configuration values for real-time matched-filter plotting.

    Works like a dictionary and can have anything added to it.
    """
    defaults = {
        "plot_length": 600.,
        "lowcut": 1.0,
        "highcut": 10.0,
    }
    readonly = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class DatabaseManagerConfig(_ConfigAttribDict):
    """
    A holder for configuration values for database management.

    Works like a dictionary and can have anything added to it.
    """
    defaults = {
        "event_path": ".",
        "event_format": "QUAKEML",
        "event_name_structure": "{event_id_end}",
        "path_structure": "{year}/{month}/{event_id_end}",
        "event_ext": ".xml",
        "min_stations": 5,
        "lookup_starttime": "1970:01:01T00:00:00.00000Z",
        # TODO: Add wavebank args.
    }
    readonly = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class TemplateConfig(_ConfigAttribDict):
    """
    A holder for configuration values for Template construction.

    Works like a dictionary and can have anything added to it.
    """
    defaults = {
        "lowcut": 2.0,
        "highcut": 15.0,
        "filt_order": 4,
        "samp_rate": 50.,
        "length": 4.,
        "prepick": .2,
        "swin": "all",
        "process_len": 300,
        "min_snr": 0,
    }
    readonly = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


KEY_MAPPER = {
    "rt_match_filter": RTMatchFilterConfig,
    "reactor": ReactorConfig,
    "plot": PlotConfig,
    "database_manager": DatabaseManagerConfig,
    "template": TemplateConfig,
}


class Config(object):
    """
    Base configuration parameters from RT_EQcorrscan.

    Parameters
    ----------
    log_level
        Any parsable string for logging.basicConfig
    log_formatter
        Any parsable string formatter for logging.basicConfig
    rt_match_filter
        Config values for real-time matched-filtering
    reactor
        Config values for the Reactor
    plot
        Config values for real-time plotting
    database_manager
        Config values for the database manager.
    template
        Config values for template creation.
    """
    def __init__(
        self,
        log_level: str = "INFO",
        log_formatter: str = "%(asctime)s\t[%(processName)s:%(threadName)s]: %(name)s\t%(levelname)s\t%(message)s",
        **kwargs
    ):
        self.rt_match_filter = RTMatchFilterConfig()
        self.reactor = ReactorConfig()
        self.plot = PlotConfig()
        self.database_manager = DatabaseManagerConfig()
        self.template = TemplateConfig()
        self.log_level = log_level
        self.log_formatter = log_formatter

        for key, value in kwargs.items():
            if key not in KEY_MAPPER.keys():
                raise NotImplementedError("Unsupported argument "
                                          "type: {0}".format(key))
            if isinstance(value, dict):
                self.__dict__[key] = KEY_MAPPER[key](value)
            else:
                assert isinstance(value, type(self.__dict__[key]))
                self.__dict__[key] = value

    def __repr__(self):
        return ("Config(\n\trt_match_filter={0},\n\treactor={1},\n\tplot={2},"
                "\n\tdatabase_manager={3},\n\ttemplate={4}".format(
                    self.rt_match_filter.__repr__(), self.reactor.__repr__(),
                    self.plot.__repr__(), self.database_manager.__repr__(),
                    self.template.__repr__()))

    def __eq__(self, other):
        if not isinstance(other, Config):
            return False
        if set(self.__dict__.keys()) != set(other.__dict__.keys()):
            return False
        for key in self.__dict__.keys():
            if not self.__dict__[key] == other.__dict__[key]:
                return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def write(self, config_file: str) -> None:
        """
        Write the configuration to a tml formatted file.

        Parameters
        ----------
        config_file
            path to the configuration file. Will overwrite and not warm
        """
        with open(config_file, "w") as f:
            f.write(dump(self.to_yaml_dict(), Dumper=Dumper))

    def to_yaml_dict(self) -> dict:
        """ Make a more human readable yaml format """
        _dict = {}
        for key, value in self.__dict__.items():
            if hasattr(value, "to_yaml_dict"):
                _dict.update({key: value.to_yaml_dict()})
            else:
                _dict.update({key: value})
        return _dict

    def setup_logging(self, **kwargs):
        """Set up logging using the logging parameters."""
        file_log_args = dict(filename="rt_eqcorrscan.log", mode='a',
                             maxBytes=20*1024*1024, backupCount=2,
                             encoding=None, delay=0)
        file_log_args.update(kwargs)
        rotating_handler = RotatingFileHandler(**file_log_args)
        rotating_handler.setFormatter(
            logging.Formatter(self.log_formatter))
        rotating_handler.setLevel(logging.DEBUG)
        # Console handler
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(
            logging.Formatter(self.log_formatter))
        # logging.basicConfig(
        #     level=self.log_level, format=self.log_formatter,
        #     handlers=[rotating_handler, console_handler])
        logging.basicConfig(
            level=self.log_level, format=self.log_formatter)


def read_config(config_file=None) -> Config:
    """
    Read configuration from a yml file.

    Parameters
    ----------
    config_file
        path to the configuration file.

    Returns
    -------
    Configuration with required defaults filled and updated based on the
    contents of the file.
    """
    if config_file is None:
        return Config()
    if not os.path.isfile(config_file):
        raise FileNotFoundError(config_file)
    with open(config_file, "rb") as f:
        configuration = load(f, Loader=Loader)
    config_dict = {}
    for key, value in configuration.items():
        if key.replace(" ", "_") in KEY_MAPPER.keys():
            config_dict.update(
                {key.replace(" ", "_"):
                     {_key.replace(" ", "_"): _value
                      for _key, _value in value.items()}})
        else:
            config_dict.update({key: value})
    return Config(**config_dict)


if __name__ == "__main__":
    import doctest

    doctest.testmod()
