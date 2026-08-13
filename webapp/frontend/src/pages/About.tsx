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
          PerfArena is a ranking of language models. It ranks them by the
          performance of the code that they write. Each competition has a
          problem set, a correctness oracle, and a performance oracle. The
          team runs each model and checks each result. Anyone can do a run
          again and get the same result. Anyone can examine the record of a
          run. <b>NYU Abu Dhabi</b> runs this project. The code has the Apache
          2.0 License.
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
          is a research group at NYU Abu Dhabi. It studies programming languages, program analysis,
          and software engineering for new AI systems. Rui Rua and Karim Ali
          are members of SanadLab. João Paulo Fernandes is a faculty member at NYU Abu Dhabi.
        </p>
      </section>

      <section>
        <h2>Citing PerfArena</h2>
        <p className="muted">
          Do you use the leaderboard or the solutions dataset in your work? Please cite this
          project and add a link to this site. The BibTeX citation is not ready yet.
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
