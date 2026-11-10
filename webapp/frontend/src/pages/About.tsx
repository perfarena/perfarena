// Update these URLs once the canonical sites are confirmed.
const SANADLAB_URL = "https://sanadlab.org";
const TEAM = [
  {
    name: "Rui Rua",
    site: "https://rrua.github.com",
    position: "Post-Doctoral Associate, NYU Abu Dhabi",
    affiliations: ["SanadLab"],
    photo: "https://rrua.github.io/images/profile.png", // drop a 240×240 photo here
  },
  {
    name: "Hailemarian Mersha",
    site: "https://sanadlab.org",
    position: "Undergraduate Research Assistant, NYU Abu Dhabi",
    affiliations: ["SanadLab"],
    photo: "https://sanadlab.org/resources/images/people/haile.png", // drop a 240×240 photo here
  },
  {
    name: "Karim Ali",
    site: "https://example.com/karim-ali",
    position: "Associate Professor, NYU Abu Dhabi",
    affiliations: ["SanadLab"],
    photo: "https://karimali.ca/resources/images/karim.jpg",
  },
  {
    name: "João Paulo Fernandes",
    site: "https://example.com/joao-paulo-fernandes",
    position: "Visiting Associate Professor, NYU Abu Dhabi",
    affiliations: [],
    photo: "https://joao30730.github.io/Site/Home_files/droppedImage.jpg",
  },
];

export default function About() {
  return (
    <div className="col" style={{ gap: 24 }}>
      <div className="hero" style={{ paddingBottom: 0 }}>
        <h1>About PerfArena</h1>
        <p style={{ maxWidth: 720, fontSize: "1.05rem", lineHeight: 1.5 }}>
          PerfArena is an open ranking of language models by the performance
          of the code they generate. Each competition pairs a problem set with
          its own correctness and performance oracles. We run every model
          ourselves and verify each result end to end. Anyone can reproduce
          or audit a run from its recorded trace. The project comes from{" "}
          <b>NYU Abu Dhabi</b>. The code is released under the Apache 2.0
          License. The data that PerfArena produces (generated solutions,
          measurements, and rankings) is released under the{" "}
          <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">
            CC BY 4.0
          </a>{" "}
          License. LeetCode problem statements remain the property of LeetCode.
        </p>
      </div>

      <section>
        <h2>The team</h2>
        <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
          {TEAM.map(member => <Author key={member.name} {...member} />)}
        </div>
      </section>

      <section>
        <h2>Affiliations</h2>
        <p className="muted" style={{ lineHeight: 1.6 }}>
          <a href={SANADLAB_URL} target="_blank" rel="noreferrer"><b>SanadLab</b></a>{" "}
          is a research group at NYU Abu Dhabi that studies programming languages, program analysis,
          and software engineering for the next generation of AI systems. Rui Rua and Karim Ali
          are affiliated with SanadLab. João Paulo Fernandes is faculty at NYU Abu Dhabi.
        </p>
      </section>

      <section>
        <h2>Citing PerfArena</h2>
        <p className="muted">
          If you use the leaderboard or the downloadable solutions dataset in your work, please cite
          the project and link back to this site. The BibTeX citation is coming soon.
        </p>
      </section>

    </div>
  );
}

function Author({
  name, site, position, affiliations, photo,
}: {
  name: string; site: string; position: string;
  affiliations: string[]; photo: string;
}) {
  return (
    <div className="card" style={{ flex: "1 1 260px", minWidth: 260, textAlign: "center" }}>
      <div className="avatar" aria-hidden>
        <img
          src={photo}
          alt=""
          onError={e => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
        />
      </div>
      <h3 style={{ margin: "12px 0 4px" }}>
        <a href={site} target="_blank" rel="noreferrer">{name}</a>
      </h3>
      <div className="muted" style={{ fontSize: "0.9rem", lineHeight: 1.4 }}>
        {position}
      </div>
      {affiliations.length > 0 && (
        <div className="muted" style={{ fontSize: "0.85rem", marginTop: 6 }}>
          {affiliations.map((r, i) => (
            <span key={r}>
              {i > 0 && " · "}
              {r === "SanadLab"
                ? <a href={SANADLAB_URL} target="_blank" rel="noreferrer">{r}</a>
                : r}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
