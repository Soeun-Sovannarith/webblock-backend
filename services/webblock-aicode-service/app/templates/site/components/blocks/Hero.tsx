export type HeroProps = { title?: string; subtitle?: string };

export default function Hero({ title = "Welcome", subtitle = "" }: HeroProps) {
  return (
    <section className="wb-section wb-hero">
      <div className="wb-container">
        <h1>{title}</h1>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
    </section>
  );
}
