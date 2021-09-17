import brickschema
import pandas as pd
import numpy as np
from operator import itemgetter
import sys
sys.path.append("/mnt/c/Users/duar3/Documents/github/smap/python")
sys.path.append("/mnt/c/Users/duar3/Documents/github/smap/python/smap")

from smap.archiver.client import SmapClient
from smap.contrib import dtutil

# create plots
from bokeh.palettes import Spectral8
from bokeh.io import show, save, output_file
from bokeh.layouts import column
from bokeh.plotting import figure, show
from bokeh.models import ColumnDataSource, RangeTool, LinearAxis, Range1d, BoxAnnotation, Legend

_debug = 1

def _query_hw_consumers(g):
    """
    Retrieve hot water consumers in the building, their respective
    boiler(s), and relevant hvac zones.
    """
    # query direct and indirect hot water consumers
    hw_consumers_query = """SELECT DISTINCT * WHERE {
    ?boiler     rdf:type/rdfs:subClassOf?   brick:Boiler .
    ?boiler     brick:feeds+                ?t_unit .
    ?t_unit     rdf:type                    ?equip_type .
    ?mid_equip  brick:feeds                 ?t_unit .
    ?t_unit     brick:feeds+                ?room_space .
    ?room_space rdf:type/rdfs:subClassOf?   brick:HVAC_Zone .

        FILTER NOT EXISTS { 
            ?subtype ^a ?t_unit ;
                (rdfs:subClassOf|^owl:equivalentClass)* ?equip_type .
            filter ( ?subtype != ?equip_type )
            }
    }
    """
    if _debug: print("Retrieving hot water consumers for each boiler.\n")

    q_result = g.query(hw_consumers_query)
    df_hw_consumers = pd.DataFrame(q_result, columns=[str(s) for s in q_result.vars])

    return df_hw_consumers


def _clean_metadata(df_hw_consumers):
    """
    Cleans metadata dataframe to have unique hot water consumers with
    most specific classes associated to other relevant information.
    """

    unique_t_units = df_hw_consumers.loc[:, "t_unit"].unique()
    direct_consumers_bool = df_hw_consumers.loc[:, 'mid_equip'] == df_hw_consumers.loc[:, 'boiler']

    direct_consumers = df_hw_consumers.loc[direct_consumers_bool, :]
    indirect_consumers = df_hw_consumers.loc[~direct_consumers_bool, :]

    # remove any direct hot consumers listed in indirect consumers
    for unit in direct_consumers.loc[:, "t_unit"].unique():
        indir_test = indirect_consumers.loc[:, "t_unit"] == unit

        # update indirect consumers df
        indirect_consumers = indirect_consumers.loc[~indir_test, :]

    # label type of hot water consumer
    direct_consumers.loc[:, "consumer_type"] = "direct"
    indirect_consumers.loc[:, "consumer_type"] = "indirect"

    hw_consumers = pd.concat([direct_consumers, indirect_consumers])
    hw_consumers = hw_consumers.drop(columns=["subtype"]).reset_index(drop=True)

    return hw_consumers


def return_entity_points(g, entity, point_list):
    """
    Return defined brick point class for piece of equipment
    """
    
    if isinstance(point_list, list):
        points = " ".join(point_list)

    # query to return certain points of other points
    term_query = f"""SELECT DISTINCT * WHERE {{
        VALUES ?req_point {{ {points} }}
        ?point_name     rdf:type                        ?req_point .
        ?point_name     brick:isPointOf                 ?t_unit .
        ?point_name     brick:bacnetPoint               ?bacnet_id .
        ?point_name     brick:hasUnit?                  ?val_unit .
        ?bacnet_id      brick:hasBacnetDeviceInstance   ?bacnet_instance .
        ?bacnet_id      brick:hasBacnetDeviceType       ?bacnet_type .
        ?bacnet_id      brick:accessedAt                ?bacnet_net .
        ?bacnet_net     dbc:connstring                  ?bacnet_addr .
        }}"""

    # execute the query
    q_result = g.query(term_query, initBindings={"t_unit": entity})

    df = pd.DataFrame(q_result, columns=[str(s) for s in q_result.vars])
    df = df.drop_duplicates(subset=['point_name']).reset_index(drop=True)

    return df


def get_paths_from_tags(tags):
    paths = {key: tags[key]["Path"] for key in tags}
    paths = pd.DataFrame.from_dict(paths, orient='index', columns=['path'])
    new_cols = ["empty", "site", "bms", "bacnet_instance", "bms2", "point_name"]

    # adjustments to dataframe
    paths[new_cols] = paths.path.str.split("/", expand=True)
    paths = paths.drop(columns=["empty"])

    return paths


def plot_multiple_entities(metadata, data, start, end, filename, exclude_str=None):

    plots = []
    for ii, point_type in enumerate(metadata['req_point'].unique()):
        # if "Position" in point_type:
        #     y_plot_range = Range1d(start=0, end=101)
        # else:
        #     y_plot_range = Range1d(start=0, end=1.1)

        if ii == 0:
            x_plot_range = (pd.to_datetime(start, unit='s'), pd.to_datetime(end, unit='s'))
        else:
            x_plot_range = plots[0].x_range

        p = figure(
            plot_height=400, plot_width=1500,
            x_axis_type="datetime", x_axis_location="below",
            x_range=x_plot_range,
            # y_range=y_plot_range
            )
        p.add_layout(Legend(), 'right')

        in_data = metadata["req_point"].isin([point_type])
        in_data_index = in_data[in_data].index
        df_subset = [data[x] for x in in_data_index]

        for i, dd in enumerate(df_subset):

            if exclude_str is not None:
                if any([nm in metadata.loc[in_data_index[i], "point_name_x"] for nm in exclude_str]):
                    continue
            p.step(
                pd.to_datetime(dd[:, 0], unit='ms'), dd[:, 1], legend_label=metadata.loc[in_data_index[i], "point_name_x"],
                color = Spectral8[i % 8], line_width=2
                )

        p.yaxis.axis_label = str(point_type)
        p.legend.click_policy = "hide"
        p.legend.label_text_font_size = "6px"
        p.legend.spacing = 1

        plots.append(p)

    output_file(filename)
    save(column(plots))

    return plots


def plot_boiler_temps(boiler_points_to_download, boiler_data):
    p = figure(
            plot_height=400, plot_width=1500,
            x_axis_type="datetime", x_axis_location="below",
            x_range=(pd.to_datetime(start, unit='s'), pd.to_datetime(end, unit='s')),
            y_range=Range1d(start=0, end=200)
            )
    p.add_layout(Legend(), 'right')

    for i, dd in enumerate(boiler_data):
        p.step(
            pd.to_datetime(dd[:, 0], unit='ms'), dd[:, 1], legend_label=boiler_points_to_download.iloc[i]["point_name_x"],
            color = Spectral8[i % 8], line_width=2
            )

    p.yaxis.axis_label = "Boiler temperatures"
    p.legend.click_policy = "hide"
    p.legend.label_text_font_size = "10px"
    p.legend.spacing = 1

    output_file("boiler_temps.html")
    save(p)

    return p


def get_data_from_smap(points_to_download, paths, smap_client, start, end):
    data_ids = points_to_download["bacnet_instance"]
    avail_to_download = paths["bacnet_instance"].isin(data_ids)
    data_paths = paths.loc[avail_to_download, :]

    # combine the data frames
    df_combine = pd.merge(data_paths.reset_index(), points_to_download, how="right", on="bacnet_instance")

    # get data from smap
    data = smap_client.data_uuid(df_combine["index"], start, end)

    return df_combine, data


if __name__ == "__main__":
    # database settings
    url = "http://178.128.64.40:8079"
    keyStr = "B7qm4nnyPVZXbSfXo14sBZ5laV7YY5vjO19G"
    where = "Metadata/SourceName = 'Field Study 4'"

    # time interval for to download data
    start = dtutil.dt2ts(dtutil.strptime_tz("9-9-2021", "%m-%d-%Y"))
    end   = dtutil.dt2ts(dtutil.strptime_tz("9-17-2021", "%m-%d-%Y"))

    # initiate smap client and download tags
    smap_client = SmapClient(url, key=keyStr)
    tags = smap_client.tags(where, asdict=True)

    # retrieve relevant tags from smap database
    paths = get_paths_from_tags(tags)

    # load schema files
    exp_brick_model_file = "../dbc_brick_expanded.ttl"
    g = brickschema.Graph()
    g.load_file(exp_brick_model_file)

    # query hot water consumers and clean metadata
    df_hw_consumers = _query_hw_consumers(g)
    df_hw_consumers = _clean_metadata(df_hw_consumers)


    #############################
    ##### Return hw consumer ctrl points
    #############################
    vlvs = ["brick:Position_Sensor", "brick:Valve_Command"]
    df_vlvs = []
    for t_unit in df_hw_consumers["t_unit"].unique():
        df_vlvs.append(return_entity_points(g, t_unit, vlvs))

    df_vlvs = pd.concat(df_vlvs).reset_index(drop=True)
    df_vlvs["bacnet_instance"] = df_vlvs["bacnet_instance"].astype(int).astype(str)

    # download data from smap
    ctrl_points_to_download, hw_ctrl_data = get_data_from_smap(df_vlvs, paths, smap_client, start, end)


    # create plot
    ctrl_plots = plot_multiple_entities(ctrl_points_to_download, hw_ctrl_data, start, end, "hw_consumer_ctrl.html", exclude_str=["REV", "DPR", "D-O"])

    #############################
    ##### Return boiler points
    #############################
    temps = [
        "brick:Hot_Water_Supply_Temperature_Sensor",
        "brick:Return_Water_Temperature_Sensor",
        "brick:Supply_Water_Temperature_Setpoint"
        ]
    df_hw_temps = []
    for boiler in df_hw_consumers["boiler"].unique():
        df_hw_temps.append(return_entity_points(g, boiler, temps))

    df_hw_temps = pd.concat(df_hw_temps).reset_index(drop=True)
    df_hw_temps["bacnet_instance"] = df_hw_temps["bacnet_instance"].astype(int).astype(str)

    # download data from smap
    boiler_points_to_download, boiler_data = get_data_from_smap(df_hw_temps, paths, smap_client, start, end)

    # create plots
    boiler_plot = plot_boiler_temps(boiler_points_to_download, boiler_data)


    #############################
    ##### Return hw consumer discharge temperatures
    #############################

    dischrg_temps = ["brick:Supply_Air_Temperature_Sensor", "brick:Embedded_Temperature_Sensor"]

    df_dischrg_temps = []
    for t_unit in df_hw_consumers["t_unit"].unique():
        df_dischrg_temps.append(return_entity_points(g, t_unit, dischrg_temps))

    df_dischrg_temps = pd.concat(df_dischrg_temps).reset_index(drop=True)
    df_dischrg_temps["bacnet_instance"] = df_dischrg_temps["bacnet_instance"].astype(int).astype(str)

    # download data from smap
    # TODO: figure out why there is a value error when downloading
    dischrg_temps_to_download1, dischrg_temps_data1 = get_data_from_smap(df_dischrg_temps.loc[:19], paths, smap_client, start, end)
    dischrg_temps_to_download2, dischrg_temps_data2 = get_data_from_smap(df_dischrg_temps.loc[20:], paths, smap_client, start, end)

    dischrg_temps_to_download = pd.concat([dischrg_temps_to_download1, dischrg_temps_to_download2]).reset_index(drop=True)
    dischrg_temps_data = dischrg_temps_data1 + dischrg_temps_data2

    # create plots
    dischrg_temps_plots = plot_multiple_entities(dischrg_temps_to_download, dischrg_temps_data, start, end, "hw_consumer_discharge_temps.html")


    #############################
    ##### Return zone temperatures
    #############################

    zone_temps = ["brick:Zone_Air_Temperature_Sensor", "brick:Air_Temperature_Setpoint"]

    df_zone_temps = []
    for zn in df_hw_consumers["room_space"]:
        df_zone_temps.append(return_entity_points(g, zn, zone_temps))

    df_zone_temps = pd.concat(df_zone_temps).reset_index(drop=True)
    df_zone_temps["bacnet_instance"] = df_zone_temps["bacnet_instance"].astype(int).astype(str)

    # download data from smap
    # TODO: figure out why there is a value error when downloading
    zn_temps_to_download1, zn_temps_data1 = get_data_from_smap(df_zone_temps.loc[:39], paths, smap_client, start, end)
    zn_temps_to_download2, zn_temps_data2 = get_data_from_smap(df_zone_temps.loc[40:69], paths, smap_client, start, end)
    zn_temps_to_download3, zn_temps_data3 = get_data_from_smap(df_zone_temps.loc[70:], paths, smap_client, start, end)

    zn_temps_to_download = pd.concat([zn_temps_to_download1, zn_temps_to_download2, zn_temps_to_download3]).reset_index(drop=True)
    zn_temps_data = zn_temps_data1 + zn_temps_data2 + zn_temps_data3

    # create plots
    air_zones = zn_temps_to_download["t_unit"].str.contains("Air_Zone")
    rad_zones = zn_temps_to_download["t_unit"].str.contains("Radiant_Zone")

    air_zone_temps_plots = plot_multiple_entities(zn_temps_to_download.loc[air_zones, :], zn_temps_data, start, end, "air_zone_temps.html")
    rad_zone_temps_plots = plot_multiple_entities(zn_temps_to_download.loc[rad_zones, :], zn_temps_data, start, end, "rad_zone_temps.html")

    import pdb; pdb.set_trace()



