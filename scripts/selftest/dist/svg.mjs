//#region src/lib/pipeline/measure.ts
function rotate(points, degrees) {
	if (degrees === 0) return [...points];
	const radians = degrees * Math.PI / 180;
	const cos = Math.cos(radians);
	const sin = Math.sin(radians);
	return points.map((p) => ({
		x: p.x * cos - p.y * sin,
		y: p.x * sin + p.y * cos
	}));
}
/** Recentres a contour on its own bounding box, in the orientation it will be
* worn. Used by the SVG export and by the frame builder, which both want the
* lens at the origin rather than wherever it happened to sit on the sheet. */
function normalisedContour(measure) {
	const boxed = rotate(measure.contourMm, measure.rotationDeg);
	const xs = boxed.map((p) => p.x);
	const ys = boxed.map((p) => p.y);
	const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
	const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
	return boxed.map((p) => ({
		x: p.x - cx,
		y: p.y - cy
	}));
}
//#endregion
//#region src/lib/exportSvg.ts
var MARGIN_MM = 4;
function contourToSvg(measure, label) {
	const points = normalisedContour(measure);
	const halfWidth = measure.widthMm / 2 + MARGIN_MM;
	const halfHeight = measure.heightMm / 2 + MARGIN_MM;
	const width = halfWidth * 2;
	const height = halfHeight * 2;
	const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${(p.x + halfWidth).toFixed(3)},${(p.y + halfHeight).toFixed(3)}`).join(" ") + " Z";
	const checkX = MARGIN_MM / 2;
	const checkY = height - MARGIN_MM / 2 - 10;
	return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="${width.toFixed(3)}mm" height="${height.toFixed(3)}mm"
     viewBox="0 0 ${width.toFixed(3)} ${height.toFixed(3)}">
  <title>OptiFrame — contour ${label} à l'échelle 1:1</title>
  <desc>A = ${measure.widthMm.toFixed(2)} mm, B = ${measure.heightMm.toFixed(2)} mm, périmètre = ${measure.perimeterMm.toFixed(2)} mm. Imprimer à 100 %, sans « ajuster à la page ».</desc>
  <g fill="none" stroke="#000" stroke-width="0.2">
    <path d="${path}"/>
    <rect x="${checkX.toFixed(3)}" y="${checkY.toFixed(3)}" width="10" height="10"/>
  </g>
  <text x="${13 .toFixed(3)}" y="${(checkY + 7).toFixed(3)}" font-family="sans-serif" font-size="3" fill="#000">carré de contrôle 10,0 mm</text>
</svg>
`;
}
var svg = contourToSvg({
	contourPx: [],
	contourMm: Array.from({ length: 240 }, (_, i) => {
		const t = 2 * Math.PI * i / 240;
		return {
			x: 100 + 24.75 * Math.cos(t),
			y: 150 + 17 * Math.sin(t)
		};
	}),
	widthMm: 49.5,
	heightMm: 34,
	perimeterMm: 132.4,
	areaMm2: 1321,
	rotationDeg: 0,
	method: "rim",
	ridgeScore: .9,
	offsetMm: 0
}, "œil droit");
var width = /width="([\d.]+)mm"/.exec(svg)?.[1];
var height = /height="([\d.]+)mm"/.exec(svg)?.[1];
var viewBox = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(svg);
console.log(`width=${width}mm height=${height}mm viewBox=${viewBox?.[1]} x ${viewBox?.[2]}`);
if (width !== viewBox?.[1] || height !== viewBox?.[2]) throw new Error("viewBox does not match physical size — the print would not be 1:1");
if (Number(width) !== 57.5) throw new Error(`expected lens width + 2*margin, got ${width}`);
var coords = [...svg.matchAll(/[ML]([\d.-]+),([\d.-]+)/g)].map((m) => [Number(m[1]), Number(m[2])]);
var xs = coords.map((c) => c[0]);
var ys = coords.map((c) => c[1]);
var spanX = Math.max(...xs) - Math.min(...xs);
var spanY = Math.max(...ys) - Math.min(...ys);
console.log(`path spans ${spanX.toFixed(3)} x ${spanY.toFixed(3)} mm (expected 49.500 x 34.000)`);
if (Math.abs(spanX - 49.5) > .01 || Math.abs(spanY - 34) > .01) throw new Error("path is not at 1:1 scale");
console.log("control square present:", svg.includes("width=\"10\" height=\"10\""));
console.log("SVG export OK");
//#endregion
export {};
