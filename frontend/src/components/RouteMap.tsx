import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";

/**
 * The workout's route on a map. Leaflet with CARTO's raster tiles (free for
 * this scale, attribution required) - dark tiles, since the map sits inside
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
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    }).addTo(m);
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
