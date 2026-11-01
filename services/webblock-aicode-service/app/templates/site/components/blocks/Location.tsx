export type LocationProps = { title?: string; address?: string; mapsUrl?: string };

export default function Location({ title = "Find us", address = "", mapsUrl = "" }: LocationProps) {
  return (
    <section className="wb-section wb-location">
      <div className="wb-container">
        <h2>{title}</h2>
        <p>{address}</p>
        {mapsUrl || address ? (
          <a
            href={mapsUrl || `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`}
            target="_blank"
            rel="noreferrer"
          >
            Open in Maps
          </a>
        ) : null}
      </div>
    </section>
  );
}
