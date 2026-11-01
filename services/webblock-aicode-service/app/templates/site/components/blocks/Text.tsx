export type TextProps = { text?: string };

export default function Text({ text = "" }: TextProps) {
  return (
    <section className="wb-section wb-text">
      <div className="wb-container">
        <p>{text}</p>
      </div>
    </section>
  );
}
