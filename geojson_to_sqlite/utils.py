from shapely.geometry import shape
import sqlite_utils

import inspect
import itertools
import json
import os

SPATIALITE_PATHS = (
    "/usr/lib/x86_64-linux-gnu/mod_spatialite.so",
    "/usr/local/lib/mod_spatialite.dylib",
)


class SpatiaLiteError(Exception):
    pass


def yield_records(features, pk, spatialite):
    for feature in features:
        record = {}
        if "id" in feature:
            record["id"] = feature["id"]
        record.update(feature.get("properties") or {})
        if spatialite:
            record["geometry"] = shape(feature["geometry"]).wkt
        else:
            record["geometry"] = feature["geometry"]
        yield record


def import_features(
    db_path,
    table,
    features,
    pk=None,
    alter=False,
    spatialite=False,
    spatialite_mod=None,
    spatial_index=False,
):
    db = sqlite_utils.Database(db_path)
    features = iter(features)

    # grab a sample, for checking ids
    sample_geojson = list(itertools.islice(features, 100))
    features = itertools.chain(sample_geojson, features)
    sample_records = list(yield_records(sample_geojson, pk, spatialite))

    if pk is None and has_ids(sample_records):
        pk = "id"

    conversions = {}
    if spatialite_mod or spatial_index:
        spatialite = True

    if spatialite:
        lib = spatialite_mod or find_spatialite()

        if not lib:
            raise SpatiaLiteError("Could not find SpatiaLite module")

        init_spatialite(db, lib)

        if table not in db.table_names():
            # Create the table, using detected column types
            column_types = sqlite_utils.suggest_column_types(sample_records)
            column_types.pop("geometry")
            db[table].create(column_types, pk=pk)
            ensure_table_has_geometry(db, table)

        conversions = {"geometry": "GeomFromText(?, 4326)"}

    if pk:
        db[table].upsert_all(
            yield_records(features, pk, spatialite),
            conversions=conversions,
            pk=pk,
            alter=alter,
        )

    else:
        db[table].insert_all(
            yield_records(features, pk, spatialite), conversions=conversions
        )

    if spatial_index:
        db.conn.execute("select CreateSpatialIndex(?, ?)", [table, "geometry"])

    return db[table]


def get_features(geojson_file, nl=False):
    """
    Get a list of features from something resembling geojson.

    Note that if the `nl` option is True, this will return a generator
    that yields a single feature from each line in the source file.
    """
    if nl:
        return (json.loads(line) for line in geojson_file if line.strip())

    # if not nl, load the whole file
    with open(geojson_file, encoding='utf-8') as fh:
        data = json.load(fh)
    geojson = data
    if not isinstance(geojson, dict):
        raise TypeError("GeoJSON root must be an object")

    if geojson.get("type") not in ("Feature", "FeatureCollection"):
        raise ValueError("GeoJSON must be a Feature or a FeatureCollection")

    if geojson["type"] == "Feature":
        return [geojson]

    return geojson.get("features", [])


def find_spatialite():
    for path in SPATIALITE_PATHS:
        if os.path.exists(path):
            return path
    return None


def init_spatialite(db, lib):
    db.conn.enable_load_extension(True)
    db.conn.load_extension(lib)
    # Initialize SpatiaLite if not yet initialized
    if "spatial_ref_sys" in db.table_names():
        return
    db.conn.execute("select InitSpatialMetadata(1)")


def ensure_table_has_geometry(db, table):
    if "geometry" not in db[table].columns_dict:
        db.conn.execute(
            "SELECT AddGeometryColumn(?, 'geometry', 4326, 'GEOMETRY', 2);", [table]
        )


def has_ids(features):
    return all(f.get("id") is not None for f in features)
