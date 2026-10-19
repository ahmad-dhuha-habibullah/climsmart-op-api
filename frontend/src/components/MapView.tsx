import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { TerraDraw, TerraDrawPolygonMode } from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import type { Plot } from "../api";

// OSM raster basemap (attribution shown by MapLibre). Satellite imagery is not wired in this slice.
const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

export function MapView({ plots, selected, onSelect, onDrawn, drawing, onDrawingChange }: {
  plots: Plot[]; selected?: string; onSelect: (id: string) => void;
  onDrawn: (geom: GeoJSON.Polygon) => void; drawing: boolean; onDrawingChange: (b: boolean) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const draw = useRef<TerraDraw | null>(null);
  const [ready, setReady] = useState(false);
  const cb = useRef({ onSelect, onDrawn, onDrawingChange });
  cb.current = { onSelect, onDrawn, onDrawingChange };

  useEffect(() => {
    const m = new maplibregl.Map({ container: el.current!, style: STYLE, center: [104.0, -3.2], zoom: 6 });
    m.addControl(new maplibregl.NavigationControl(), "top-right");
    m.addControl(new maplibregl.ScaleControl({}), "bottom-left");
    m.on("load", () => {
      m.addSource("plots", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "plots-fill", type: "fill", source: "plots", paint: { "fill-color": ["case", ["get", "sel"], "#267653", "#a9c4b8"], "fill-opacity": 0.45 } });
      m.addLayer({ id: "plots-line", type: "line", source: "plots", paint: { "line-color": "#173f35", "line-width": ["case", ["get", "sel"], 3, 1.5] } });
      m.on("click", "plots-fill", (e) => { const id = e.features?.[0]?.properties?.id; if (id && !draw.current?.enabled) cb.current.onSelect(id); });
      m.on("mouseenter", "plots-fill", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "plots-fill", () => (m.getCanvas().style.cursor = ""));
      setReady(true);
    });
    map.current = m;
    return () => { draw.current?.stop(); m.remove(); };
  }, []);

  // plots layer
  useEffect(() => {
    const m = map.current; if (!ready || !m) return;
    const fc = { type: "FeatureCollection", features: plots.filter((p) => p.geometry).map((p) => ({ type: "Feature", geometry: p.geometry, properties: { id: p.id, name: p.name, sel: p.id === selected } })) };
    (m.getSource("plots") as maplibregl.GeoJSONSource).setData(fc as any);
  }, [plots, selected, ready]);

  // fit to all plots when the list changes; zoom to the selected one
  useEffect(() => {
    const m = map.current; if (!ready || !m) return;
    const target = selected ? plots.filter((p) => p.id === selected) : plots;
    if (!target.length) return;
    const b = target.reduce((a, p) => [Math.min(a[0], p.bbox[0]), Math.min(a[1], p.bbox[1]), Math.max(a[2], p.bbox[2]), Math.max(a[3], p.bbox[3])], [180, 90, -180, -90]);
    m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, maxZoom: 16, duration: 400 });
  }, [plots.map((p) => p.id).join(), selected, ready]);

  // draw mode
  useEffect(() => {
    const m = map.current; if (!ready || !m) return;
    if (drawing) {
      const td = new TerraDraw({ adapter: new TerraDrawMapLibreGLAdapter({ map: m }), modes: [new TerraDrawPolygonMode()] });
      td.start(); td.setMode("polygon");
      td.on("finish", (id) => {
        const f = td.getSnapshotFeature(id);
        if (f && f.geometry.type === "Polygon") cb.current.onDrawn(f.geometry as GeoJSON.Polygon);
        td.stop(); draw.current = null; cb.current.onDrawingChange(false);
      });
      draw.current = td;
    } else if (draw.current) { draw.current.stop(); draw.current = null; }
  }, [drawing, ready]);

  return (
    <div className="map" ref={el}>
      <div className="tools">
        <button className="btn small" onClick={() => onDrawingChange(!drawing)} aria-pressed={drawing}>{drawing ? "Cancel drawing" : "Draw plot"}</button>
      </div>
      {drawing && <div className="banner" style={{ position: "absolute", bottom: 30, left: 8, zIndex: 2 }}>Click to add vertices; click the first vertex (or double-click) to close the polygon.</div>}
    </div>
  );
}
