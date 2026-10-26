import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";

/**
 * Map tiles. CARTO's free basemaps now answer "API KEY REQUIRED", so:
 *  - with VITE_MAPTILER_KEY set at build time: MapTiler's dark "dataviz" style
 *    (free tier, key from cloud.maptiler.com - restrict it to our domain);
 *  - without a key: OpenStreetMap's own tiles, darkened in CSS (.route-map.osm).
 *    Fine for a small private bot; OSM asks heavy users to bring their own provider.
 */
const MAPTILER_KEY = import.meta.env.VITE_MAPTILER_KEY as string | undefined;
const OSM = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

function tiles(): L.TileLayer {
  if (MAPTILER_KEY) {
    return L.tileLayer(`https://api.maptiler.com/maps/dataviz-dark/256/{z}/{x}/{y}{r}.png?key=${MAPTILER_KEY}`, {
      maxZoom: 19,
      attribution: `&copy; <a href="https://www.maptiler.com/copyright/">MapTiler</a> ${OSM}`,
    });
  }
  return L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    className: "osm-dark",
    attribution: OSM,
  });
}

/**
 * The workout's route on a map. Leaflet with dark tiles (see tiles() above), since the map sits inside
 * the dark summary card. The start and finish dots sit at the edge of the
 * private circles: the track arrives already trimmed (activity_detail.py).
 *
 * `cursor` is a point to mark - set while a finger moves over a chart.
 */
export function RouteMap({ track, color, cursor, height }: {
  track: [number, number][];
  color: string;
  cursor: [number, number] | null;
  height: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const marker = useRef<L.CircleMarker | null>(null);

  useEffect(() => {
    if (!box.current || track.length < 2) return;
    const m = L.map(box.current, {
      zoomControl: false,
      attributionControl: true,
      // Two fingers to pan, so the page still scrolls past the map with one.
      dragging: !L.Browser.mobile,
      scrollWheelZoom: false,
      tap: false,
    } as L.MapOptions);
    tiles().addTo(m);
    const line = L.polyline(track, { color, weight: 4, opacity: 0.95, lineCap: "round", lineJoin: "round" }).addTo(m);
    L.circleMarker(track[0], { radius: 6, color: "#fff", weight: 2, fillColor: "#111", fillOpacity: 1 }).addTo(m);
    L.circleMarker(track[track.length - 1], { radius: 6, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1 }).addTo(m);
    m.fitBounds(line.getBounds(), { padding: [18, 18] });
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
      marker.current = null;
    };
  }, [track, color]);

  // Height changes (the expand button) need Leaflet to re-measure.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const id = window.setTimeout(() => {
      m.invalidateSize();
      m.fitBounds(L.latLngBounds(track), { padding: [18, 18] });
    }, 220);
    return () => window.clearTimeout(id);
  }, [height, track]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (!cursor) {
      marker.current?.remove();
      marker.current = null;
      return;
    }
    if (!marker.current) {
      marker.current = L.circleMarker(cursor, { radius: 7, color: "#fff", weight: 3, fillColor: color, fillOpacity: 1 }).addTo(m);
    } else {
      marker.current.setLatLng(cursor);
    }
  }, [cursor, color]);

  return <div ref={box} dir="ltr" className="route-map" style={{ height }} />;
}
