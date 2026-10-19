export function NotBuilt({ title, note }: { title: string; note: string }) {
  return (
    <div className="pending-page">
      <h1>{title}</h1>
      <div className="state warn"><b>Not yet implemented in v2</b>{note}</div>
      <p className="muted">Per the design rule “nothing pending pretends to run”, this section shows no placeholder data.</p>
    </div>
  );
}
