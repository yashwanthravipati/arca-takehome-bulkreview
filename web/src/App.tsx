import { useEffect, useState } from "react";
import { ReviewList } from "./components/ReviewList";
import { ReviewPage } from "./components/ReviewPage";

// Pages, addressed by the URL hash: #/ (review list), #/reviews/<id> (grid),
// #/reviews/<id>/cells/<cellId> (grid with that cell's drawer open: a link to one answer) and
// #/reviews/<id>/cells/<cellId>/evidence (that answer checked against its document).
function useHash() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const [, reviewId, cellId, evidence] = useHash().match(/^#\/reviews\/(\d+)(?:\/cells\/(\d+)(\/evidence)?)?/) ?? [];
  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">
          Bulk Review
        </a>
        <span className="topbar-note">Regulatory document review with cited answers</span>
      </header>
      {reviewId ? (
        <ReviewPage key={reviewId} id={Number(reviewId)} cellId={cellId ? Number(cellId) : null} evidence={!!evidence} />
      ) : (
        <ReviewList />
      )}
    </div>
  );
}
