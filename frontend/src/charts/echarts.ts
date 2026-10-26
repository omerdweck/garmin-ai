/**
 * ECharts, trimmed to what the dashboard draws. Importing "echarts" whole
 * ships every chart type (~1 MB); this keeps the phone download small.
 * A chart type or component used anywhere must be registered here.
 */

import { BarChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  CalendarComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  BarChart,
  LineChart,
  HeatmapChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  MarkAreaComponent,
  CalendarComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

export { echarts };
export type { EChartsCoreOption as ChartOption } from "echarts/core";
