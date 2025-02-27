/*
 * This software is licensed under the Apache 2 license, quoted below.
 *
 * Copyright 2021 Azavea, Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License"); you may not
 * use this file except in compliance with the License. You may obtain a copy of
 * the License at
 *
 *     [http://www.apache.org/licenses/LICENSE-2.0]
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
 * License for the specific language governing permissions and limitations under
 * the License.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 */
package org.locationtech.rasterframes.ref

import geotrellis.raster.GridBounds

case class Subgrid(colMin: Int, rowMin: Int, colMax: Int, rowMax: Int) {
    def toGridBounds: GridBounds[Int] =
    GridBounds(colMin, rowMin, colMax, rowMax)
}

object Subgrid {
  def apply(grid: GridBounds[Int]): Subgrid =
    Subgrid(grid.colMin, grid.rowMin, grid.colMax, grid.rowMax)
}