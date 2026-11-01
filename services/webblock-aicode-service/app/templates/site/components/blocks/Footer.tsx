export type FooterProps = { text?: string };

export default function Footer({ text = "" }: FooterProps) {
  return (
    <footer className="wb-footer">
      <div className="wb-container">
        <p>{text}</p>
      </div>
    </footer>
  );
}
