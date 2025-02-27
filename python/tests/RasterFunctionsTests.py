#
# This software is licensed under the Apache 2 license, quoted below.
#
# Copyright 2019 Astraea, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
# [http://www.apache.org/licenses/LICENSE-2.0]
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.
#
# SPDX-License-Identifier: Apache-2.0
#

import os

import numpy as np
import pyspark.sql.functions as F
import pytest
from deprecation import fail_if_not_removed
from numpy.testing import assert_allclose, assert_equal
from pyrasterframes.rasterfunctions import *
from pyrasterframes.rf_types import *
from pyrasterframes.rf_types import CellType, Tile
from pyrasterframes.utils import gdal_version
from pyspark.sql import Row

from .conftest import assert_png, rounded_compare


# @pytest.mark.filterwarnings("ignore")
def test_setup(spark):
    assert (
        spark.sparkContext.getConf().get("spark.serializer")
        == "org.apache.spark.serializer.KryoSerializer"
    )
    print("GDAL version", gdal_version())


def test_identify_columns(rf):
    cols = rf.tile_columns()
    assert len(cols) == 1, "`tileColumns` did not find the proper number of columns."
    print("Tile columns: ", cols)
    col = rf.spatial_key_column()
    assert isinstance(col, Column), "`spatialKeyColumn` was not found"
    print("Spatial key column: ", col)
    col = rf.temporal_key_column()
    assert col is None, "`temporalKeyColumn` should be `None`"
    print("Temporal key column: ", col)


def test_tile_creation(spark):

    base = spark.createDataFrame([1, 2, 3, 4], "integer")
    tiles = base.select(
        rf_make_constant_tile(3, 3, 3, "int32"),
        rf_make_zeros_tile(3, 3, "int32"),
        rf_make_ones_tile(3, 3, CellType.int32()),
    )
    tiles.show()
    assert tiles.count() == 4


def test_multi_column_operations(rf):
    df1 = rf.withColumnRenamed("tile", "t1").as_layer()
    df2 = rf.withColumnRenamed("tile", "t2").as_layer()
    df3 = df1.spatial_join(df2).as_layer()
    df3 = df3.withColumn("norm_diff", rf_normalized_difference("t1", "t2"))
    # df3.printSchema()

    aggs = df3.agg(
        rf_agg_mean("norm_diff"),
    )
    aggs.show()
    row = aggs.first()

    assert rounded_compare(row["rf_agg_mean(norm_diff)"], 0)


def test_general(rf):
    meta = rf.tile_layer_metadata()
    assert meta["bounds"] is not None
    df = (
        rf.withColumn("dims", rf_dimensions("tile"))
        .withColumn("type", rf_cell_type("tile"))
        .withColumn("dCells", rf_data_cells("tile"))
        .withColumn("ndCells", rf_no_data_cells("tile"))
        .withColumn("min", rf_tile_min("tile"))
        .withColumn("max", rf_tile_max("tile"))
        .withColumn("mean", rf_tile_mean("tile"))
        .withColumn("sum", rf_tile_sum("tile"))
        .withColumn("stats", rf_tile_stats("tile"))
        .withColumn("extent", st_extent("geometry"))
        .withColumn("extent_geom1", st_geometry("extent"))
        .withColumn("ascii", rf_render_ascii("tile"))
        .withColumn("log", rf_log("tile"))
        .withColumn("exp", rf_exp("tile"))
        .withColumn("expm1", rf_expm1("tile"))
        .withColumn("sqrt", rf_sqrt("tile"))
        .withColumn("round", rf_round("tile"))
        .withColumn("abs", rf_abs("tile"))
    )

    df.first()


def test_st_geometry_from_struct(spark):

    df = spark.createDataFrame([Row(xmin=0, ymin=1, xmax=2, ymax=3)])
    df2 = df.select(st_geometry(F.struct(df.xmin, df.ymin, df.xmax, df.ymax)).alias("geom"))

    actual_bounds = df2.first()["geom"].bounds
    assert (0.0, 1.0, 2.0, 3.0) == actual_bounds


def test_agg_mean(rf):
    mean = rf.agg(rf_agg_mean("tile")).first()["rf_agg_mean(tile)"]
    assert rounded_compare(mean, 10160)


def test_agg_local_mean(spark):

    # this is really testing the nodata propagation in the agg  local summation
    ct = CellType.int8().with_no_data_value(4)
    df = spark.createDataFrame(
        [
            Row(tile=Tile(np.array([[1, 2, 3, 4, 5, 6]]), ct)),
            Row(tile=Tile(np.array([[1, 2, 4, 3, 5, 6]]), ct)),
        ]
    )

    result = df.agg(rf_agg_local_mean("tile").alias("mean")).first().mean

    expected = Tile(np.array([[1.0, 2.0, 3.0, 3.0, 5.0, 6.0]]), CellType.float64())
    assert result == expected


def test_aggregations(rf):
    aggs = rf.agg(
        rf_agg_data_cells("tile"),
        rf_agg_no_data_cells("tile"),
        rf_agg_stats("tile"),
        rf_agg_approx_histogram("tile"),
    )
    row = aggs.first()

    # print(row['rf_agg_data_cells(tile)'])
    assert row["rf_agg_data_cells(tile)"] == 387000
    assert row["rf_agg_no_data_cells(tile)"] == 1000
    assert row["rf_agg_stats(tile)"].data_cells == row["rf_agg_data_cells(tile)"]


@fail_if_not_removed
def test_add_scalar(rf):
    # Trivial test to trigger the deprecation failure at the right time.
    result: Row = rf.select(rf_local_add_double("tile", 99.9), rf_local_add_int("tile", 42)).first()
    assert True


def test_agg_approx_quantiles(rf):
    agg = rf.agg(rf_agg_approx_quantiles("tile", [0.1, 0.5, 0.9, 0.98]))
    result = agg.first()[0]
    # expected result from computing in external python process; c.f. scala tests
    assert_allclose(result, np.array([7963.0, 10068.0, 12160.0, 14366.0]))


def test_sql(spark, rf):

    rf.createOrReplaceTempView("rf_test_sql")

    arith = spark.sql(
        """SELECT tile,
                                rf_local_add(tile, 1) AS add_one,
                                rf_local_subtract(tile, 1) AS less_one,
                                rf_local_multiply(tile, 2) AS times_two,
                                rf_local_divide(
                                    rf_convert_cell_type(tile, "float32"),
                                    2) AS over_two
                            FROM rf_test_sql"""
    )

    arith.createOrReplaceTempView("rf_test_sql_1")
    arith.show(truncate=False)
    stats = spark.sql(
        """
            SELECT rf_tile_mean(tile) as base,
                rf_tile_mean(add_one) as plus_one,
                rf_tile_mean(less_one) as minus_one,
                rf_tile_mean(times_two) as double,
                rf_tile_mean(over_two) as half,
                rf_no_data_cells(tile) as nd

            FROM rf_test_sql_1
            ORDER BY rf_no_data_cells(tile)
            """
    )
    stats.show(truncate=False)
    stats.createOrReplaceTempView("rf_test_sql_stats")

    compare = spark.sql(
        """
            SELECT
                plus_one - 1.0 = base as add,
                minus_one + 1.0 = base as subtract,
                double / 2.0 = base as multiply,
                half * 2.0 = base as divide,
                nd
            FROM rf_test_sql_stats
            """
    )

    expect_row1 = compare.orderBy("nd").first()

    assert expect_row1.subtract
    assert expect_row1.multiply
    assert expect_row1.divide
    assert expect_row1.nd == 0
    assert expect_row1.add

    expect_row2 = compare.orderBy("nd", ascending=False).first()

    assert expect_row2.subtract
    assert expect_row2.multiply
    assert expect_row2.divide
    assert expect_row2.nd > 0
    assert expect_row2.add  # <-- Would fail in a case where ND + 1 = 1


def test_explode(rf):

    rf.select("spatial_key", rf_explode_tiles("tile")).show()
    # +-----------+------------+---------+-------+
    # |spatial_key|column_index|row_index|tile   |
    # +-----------+------------+---------+-------+
    # |[2,1]      |4           |0        |10150.0|
    cell = (
        rf.select(rf.spatial_key_column(), rf_explode_tiles(rf.tile))
        .where(F.col("spatial_key.col") == 2)
        .where(F.col("spatial_key.row") == 1)
        .where(F.col("column_index") == 4)
        .where(F.col("row_index") == 0)
        .select(F.col("tile"))
        .collect()[0][0]
    )
    assert cell == 10150.0

    # Test the sample version
    frac = 0.01
    sample_count = rf.select(rf_explode_tiles_sample(frac, 1872, "tile")).count()
    print("Sample count is {}".format(sample_count))
    assert sample_count > 0
    assert sample_count < (frac * 1.1) * 387000  # give some wiggle room


def test_mask_by_value(rf):

    # create an artificial mask for values > 25000; masking value will be 4
    mask_value = 4

    rf1 = rf.select(
        rf.tile,
        rf_local_multiply(
            rf_convert_cell_type(rf_local_greater(rf.tile, 25000), "uint8"),
            F.lit(mask_value),
        ).alias("mask"),
    )
    rf2 = rf1.select(
        rf1.tile, rf_mask_by_value(rf1.tile, rf1.mask, F.lit(mask_value), False).alias("masked")
    )

    result = rf2.agg(rf_agg_no_data_cells(rf2.tile) < rf_agg_no_data_cells(rf2.masked)).collect()[
        0
    ][0]
    assert result

    # note supplying a `int` here, not a column to mask value
    rf3 = rf1.select(
        rf1.tile,
        rf_inverse_mask_by_value(rf1.tile, rf1.mask, mask_value).alias("masked"),
        rf_mask_by_value(rf1.tile, rf1.mask, mask_value, True).alias("masked2"),
    )
    result = rf3.agg(
        rf_agg_no_data_cells(rf3.tile) < rf_agg_no_data_cells(rf3.masked),
        rf_agg_no_data_cells(rf3.tile) < rf_agg_no_data_cells(rf3.masked2),
    ).first()
    assert result[0]
    assert result[1]  # inverse mask arg gives equivalent result

    result_equiv_tiles = rf3.select(rf_for_all(rf_local_equal(rf3.masked, rf3.masked2))).first()[0]
    assert result_equiv_tiles  # inverse fn and inverse arg produce same Tile


def test_mask_by_values(spark):

    tile = Tile(np.random.randint(1, 100, (5, 5)), CellType.uint8())
    mask_tile = Tile(np.array(range(1, 26), "uint8").reshape(5, 5))
    expected_diag_nd = Tile(np.ma.masked_array(tile.cells, mask=np.eye(5)))

    df = spark.createDataFrame([Row(t=tile, m=mask_tile)]).select(
        rf_mask_by_values("t", "m", [0, 6, 12, 18, 24])
    )  # values on the diagonal
    result0 = df.first()
    # assert_equal(result0[0].cells, expected_diag_nd)
    assert result0[0] == expected_diag_nd


def test_mask_bits(spark):
    t = Tile(42 * np.ones((4, 4), "uint16"), CellType.uint16())
    # with a varitey of known values
    mask = Tile(
        np.array(
            [
                [1, 1, 2720, 2720],
                [1, 6816, 6816, 2756],
                [2720, 2720, 6900, 2720],
                [2720, 6900, 6816, 1],
            ]
        ),
        CellType("uint16raw"),
    )

    df = spark.createDataFrame([Row(t=t, mask=mask)])

    # removes fill value 1
    mask_fill_df = df.select(rf_mask_by_bit("t", "mask", 0, True).alias("mbb"))
    mask_fill_tile = mask_fill_df.first()["mbb"]

    assert mask_fill_tile.cell_type.has_no_data()

    assert mask_fill_df.select(rf_data_cells("mbb")).first()[0], 16 - 4

    # mask out 6816, 6900
    mask_med_hi_cir = (
        df.withColumn("mask_cir_mh", rf_mask_by_bits("t", "mask", 11, 2, [2, 3]))
        .first()["mask_cir_mh"]
        .cells
    )

    assert mask_med_hi_cir.mask.sum() == 5


@pytest.mark.skip("Issue #422 https://github.com/locationtech/rasterframes/issues/422")
def test_mask_and_deser(spark):
    # duplicates much of test_mask_bits but
    t = Tile(42 * np.ones((4, 4), "uint16"), CellType.uint16())
    # with a varitey of known values
    mask = Tile(
        np.array(
            [
                [1, 1, 2720, 2720],
                [1, 6816, 6816, 2756],
                [2720, 2720, 6900, 2720],
                [2720, 6900, 6816, 1],
            ]
        ),
        CellType("uint16raw"),
    )

    df = spark.createDataFrame([Row(t=t, mask=mask)])

    # removes fill value 1
    mask_fill_df = df.select(rf_mask_by_bit("t", "mask", 0, True).alias("mbb"))
    mask_fill_tile = mask_fill_df.first()["mbb"]

    assert mask_fill_tile.cell_type.has_no_data()

    # Unsure why this fails. mask_fill_tile.cells is all 42 unmasked.
    assert mask_fill_tile.cells.mask.sum() == 4, (
        f"Expected {16 - 4} data values but got the masked tile:" f"{mask_fill_tile}"
    )


def test_mask(spark):

    np.random.seed(999)
    # importantly exclude 0 from teh range because that's the nodata value for the `data_tile`'s cell type
    ma = np.ma.array(
        np.random.randint(1, 10, (5, 5), dtype="int8"), mask=np.random.rand(5, 5) > 0.7
    )
    expected_data_values = ma.compressed().size
    expected_no_data_values = ma.size - expected_data_values
    assert expected_data_values > 0, "Make sure random seed is cooperative "
    assert expected_no_data_values > 0, "Make sure random seed is cooperative "

    data_tile = Tile(np.ones(ma.shape, ma.dtype), CellType.uint8())

    df = spark.createDataFrame([Row(t=data_tile, m=Tile(ma))]).withColumn(
        "masked_t", rf_mask("t", "m")
    )

    result = df.select(rf_data_cells("masked_t")).first()[0]
    assert (
        result == expected_data_values
    ), f"Masked tile should have {expected_data_values} data values but found: {df.select('masked_t').first()[0].cells}. Original data: {data_tile.cells} Masked by {ma}"

    nd_result = df.select(rf_no_data_cells("masked_t")).first()[0]
    assert nd_result == expected_no_data_values

    # deser of tile is correct
    assert df.select("masked_t").first()[0].cells.compressed().size == expected_data_values


def test_extract_bits(spark):
    one = np.ones((6, 6), "uint8")
    t = Tile(84 * one)
    df = spark.createDataFrame([Row(t=t)])
    result_py_literals = df.select(rf_local_extract_bits("t", 2, 3)).first()[0]
    # expect value binary 84 => 1010100 => 101
    assert_equal(result_py_literals.cells, 5 * one)

    result_cols = df.select(rf_local_extract_bits("t", lit(2), lit(3))).first()[0]
    assert_equal(result_cols.cells, 5 * one)


def test_resample(rf):

    result = rf.select(
        rf_tile_min(
            rf_local_equal(rf_resample(rf_resample(rf.tile, F.lit(2)), F.lit(0.5)), rf.tile)
        )
    ).collect()[0][0]

    assert result == 1  # short hand for all values are true


def test_exists_for_all(rf):
    df = rf.withColumn("should_exist", rf_make_ones_tile(5, 5, "int8")).withColumn(
        "should_not_exist", rf_make_zeros_tile(5, 5, "int8")
    )

    should_exist = df.select(rf_exists(df.should_exist).alias("se")).take(1)[0].se
    assert should_exist

    should_not_exist = df.select(rf_exists(df.should_not_exist).alias("se")).take(1)[0].se
    assert not should_not_exist

    assert df.select(rf_for_all(df.should_exist).alias("se")).take(1)[0].se
    assert not df.select(rf_for_all(df.should_not_exist).alias("se")).take(1)[0].se


def test_cell_type_in_functions(rf):

    ct = CellType.float32().with_no_data_value(-999)

    df = (
        rf.withColumn("ct_str", rf_convert_cell_type("tile", ct.cell_type_name))
        .withColumn("ct", rf_convert_cell_type("tile", ct))
        .withColumn("make", rf_make_constant_tile(99, 3, 4, CellType.int8()))
        .withColumn("make2", rf_with_no_data("make", 99))
    )

    result = df.select("ct", "ct_str", "make", "make2").first()

    assert result["ct"].cell_type == ct
    assert result["ct_str"].cell_type == ct
    assert result["make"].cell_type == CellType.int8()

    counts = df.select(
        rf_no_data_cells("make").alias("nodata1"),
        rf_data_cells("make").alias("data1"),
        rf_no_data_cells("make2").alias("nodata2"),
        rf_data_cells("make2").alias("data2"),
    ).first()

    assert counts["data1"] == 3 * 4
    assert counts["nodata1"] == 0
    assert counts["data2"] == 0
    assert counts["nodata2"] == 3 * 4
    assert result["make2"].cell_type == CellType.int8().with_no_data_value(99)


#


def test_render_composite(spark, resource_dir):
    def l8band_uri(band_index):
        return "file://" + os.path.join(resource_dir, "L8-B{}-Elkton-VA.tiff".format(band_index))

    cat = spark.createDataFrame([Row(red=l8band_uri(4), green=l8band_uri(3), blue=l8band_uri(2))])
    rf = spark.read.raster(cat, catalog_col_names=cat.columns)

    # Test composite construction
    rgb = rf.select(rf_tile(rf_rgb_composite("red", "green", "blue")).alias("rgb")).first()["rgb"]

    # TODO: how to better test this?
    assert isinstance(rgb, Tile)
    assert rgb.dimensions() == [186, 169]

    ## Test PNG generation
    png_bytes = rf.select(rf_render_png("red", "green", "blue").alias("png")).first()["png"]
    # Look for the PNG magic cookie
    assert_png(png_bytes)


def test_rf_interpret_cell_type_as(spark):

    df = spark.createDataFrame(
        [Row(t=Tile(np.array([[1, 3, 4], [5, 0, 3]]), CellType.uint8().with_no_data_value(5)))]
    )
    df = df.withColumn("tile", rf_interpret_cell_type_as("t", "uint8ud3"))  # threes become ND
    result = df.select(rf_tile_sum(rf_local_equal("t", lit(3))).alias("threes")).first()["threes"]
    assert result == 2

    result_5 = df.select(rf_tile_sum(rf_local_equal("t", lit(5))).alias("fives")).first()["fives"]
    assert result_5 == 0


def test_rf_local_data_and_no_data(spark):

    nd = 5
    t = Tile(np.array([[1, 3, 4], [nd, 0, 3]]), CellType.uint8().with_no_data_value(nd))
    # note the convert is due to issue #188
    df = (
        spark.createDataFrame([Row(t=t)])
        .withColumn("lnd", rf_convert_cell_type(rf_local_no_data("t"), "uint8"))
        .withColumn("ld", rf_convert_cell_type(rf_local_data("t"), "uint8"))
    )

    result = df.first()
    result_nd = result["lnd"]
    assert_equal(result_nd.cells, t.cells.mask)

    result_d = result["ld"]
    assert_equal(result_d.cells, np.invert(t.cells.mask))


def test_rf_local_is_in(spark):

    nd = 5
    t = Tile(np.array([[1, 3, 4], [nd, 0, 3]]), CellType.uint8().with_no_data_value(nd))
    # note the convert is due to issue #188
    df = (
        spark.createDataFrame([Row(t=t)])
        .withColumn("a", F.array(F.lit(3), lit(4)))
        .withColumn(
            "in2",
            rf_convert_cell_type(rf_local_is_in(F.col("t"), F.array(lit(0), lit(4))), "uint8"),
        )
        .withColumn("in3", rf_convert_cell_type(rf_local_is_in("t", "a"), "uint8"))
        .withColumn(
            "in4",
            rf_convert_cell_type(rf_local_is_in("t", F.array(lit(0), lit(4), lit(3))), "uint8"),
        )
        .withColumn("in_list", rf_convert_cell_type(rf_local_is_in(F.col("t"), [4, 1]), "uint8"))
    )

    result = df.first()
    assert result["in2"].cells.sum() == 2
    assert_equal(result["in2"].cells, np.isin(t.cells, np.array([0, 4])))
    assert result["in3"].cells.sum() == 3
    assert result["in4"].cells.sum() == 4
    assert (
        result["in_list"].cells.sum() == 2
    ), "Tile value {} should contain two 1s as: [[1, 0, 1],[0, 0, 0]]".format(
        result["in_list"].cells
    )


def test_local_min_max_clamp(spark):
    tile = Tile(np.random.randint(-20, 20, (10, 10)), CellType.int8())
    min_tile = Tile(np.random.randint(-20, 0, (10, 10)), CellType.int8())
    max_tile = Tile(np.random.randint(0, 20, (10, 10)), CellType.int8())

    df = spark.createDataFrame([Row(t=tile, mn=min_tile, mx=max_tile)])
    assert_equal(
        df.select(rf_local_min("t", "mn")).first()[0].cells,
        np.clip(tile.cells, None, min_tile.cells),
    )

    assert_equal(df.select(rf_local_min("t", -5)).first()[0].cells, np.clip(tile.cells, None, -5))

    assert_equal(
        df.select(rf_local_max("t", "mx")).first()[0].cells,
        np.clip(tile.cells, max_tile.cells, None),
    )

    assert_equal(df.select(rf_local_max("t", 5)).first()[0].cells, np.clip(tile.cells, 5, None))

    assert_equal(
        df.select(rf_local_clamp("t", "mn", "mx")).first()[0].cells,
        np.clip(tile.cells, min_tile.cells, max_tile.cells),
    )


def test_rf_where(spark):
    cond = Tile(np.random.binomial(1, 0.35, (10, 10)), CellType.uint8())
    x = Tile(np.random.randint(-20, 10, (10, 10)), CellType.int8())
    y = Tile(np.random.randint(0, 30, (10, 10)), CellType.int8())

    df = spark.createDataFrame([Row(cond=cond, x=x, y=y)])
    result = df.select(rf_where("cond", "x", "y")).first()[0].cells
    assert_equal(result, np.where(cond.cells, x.cells, y.cells))


def test_rf_standardize(prdf):

    stats = (
        prdf.select(rf_agg_stats("proj_raster").alias("stat"))
        .select("stat.mean", F.sqrt("stat.variance").alias("sttdev"))
        .first()
    )

    result = (
        prdf.select(rf_standardize("proj_raster", stats[0], stats[1]).alias("z"))
        .select(rf_agg_stats("z").alias("z_stat"))
        .select("z_stat.mean", "z_stat.variance")
        .first()
    )

    assert result[0] == pytest.approx(0.0, abs=0.00001)
    assert result[1] == pytest.approx(1.0, abs=0.00001)


def test_rf_standardize_per_tile(spark):

    # 10k samples so should be pretty stable
    x = Tile(np.random.randint(-20, 0, (100, 100)), CellType.int8())
    df = spark.createDataFrame([Row(x=x)])

    result = (
        df.select(rf_standardize("x").alias("z"))
        .select(rf_agg_stats("z").alias("z_stat"))
        .select("z_stat.mean", "z_stat.variance")
        .first()
    )

    assert result[0] == pytest.approx(0.0, abs=0.00001)
    assert result[1] == pytest.approx(1.0, abs=0.00001)


def test_rf_rescale(spark):

    x1 = Tile(np.random.randint(-60, 12, (10, 10)), CellType.int8())
    x2 = Tile(np.random.randint(15, 122, (10, 10)), CellType.int8())
    df = spark.createDataFrame([Row(x=x1), Row(x=x2)])
    # Note there will be some clipping
    rescaled = df.select(rf_rescale("x", -20, 50).alias("x_prime"), "x")
    result = rescaled.agg(F.max(rf_tile_min("x_prime")), F.min(rf_tile_max("x_prime"))).first()

    assert (
        result[0] > 0.0
    ), f"Expected max tile_min to be > 0 (strictly); but it is {rescaled.select('x', 'x_prime', rf_tile_min('x_prime')).take(2)}"

    assert (
        result[1] < 1.0
    ), f"Expected min tile_max to be < 1 (strictly); it is {rescaled.select(rf_tile_max('x_prime')).take(2)}"


def test_rf_rescale_per_tile(spark):
    x1 = Tile(np.random.randint(-20, 42, (10, 10)), CellType.int8())
    x2 = Tile(np.random.randint(20, 242, (10, 10)), CellType.int8())
    df = spark.createDataFrame([Row(x=x1), Row(x=x2)])
    result = (
        df.select(rf_rescale("x").alias("x_prime"))
        .agg(rf_agg_stats("x_prime").alias("stat"))
        .select("stat.min", "stat.max")
        .first()
    )

    assert result[0] == 0.0
    assert result[1] == 1.0


def test_rf_agg_overview_raster(prdf):
    width = 500
    height = 400
    agg = prdf.select(rf_agg_extent(rf_extent(prdf.proj_raster)).alias("extent")).first().extent
    crs = prdf.select(rf_crs(prdf.proj_raster).alias("crs")).first().crs.crsProj4
    aoi = Extent.from_row(agg)
    aoi = aoi.reproject(crs, "EPSG:3857")
    aoi = aoi.buffer(-(aoi.width * 0.2))

    ovr = prdf.select(rf_agg_overview_raster(prdf.proj_raster, width, height, aoi).alias("agg"))
    png = ovr.select(rf_render_color_ramp_png("agg", "Greyscale64")).first()[0]
    assert_png(png)

    # with open('/tmp/test_rf_agg_overview_raster.png', 'wb') as f:
    #     f.write(png)


def test_rf_proj_raster(prdf):
    df = prdf.select(
        rf_proj_raster(
            rf_tile("proj_raster"), rf_extent("proj_raster"), rf_crs("proj_raster")
        ).alias("roll_your_own")
    )
    assert "extent" in df.schema["roll_your_own"].dataType.fieldNames()
