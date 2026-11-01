export type AboutProps = { title?: string; text?: string };

export default function About({ title = "About", text = "" }: AboutProps) {
  return (
    <section className="wb-section wb-about">
      <div className="wb-container">
        <h2>{title}</h2>
        <p>{text}</p>
      </div>
    </section>
  );
}
