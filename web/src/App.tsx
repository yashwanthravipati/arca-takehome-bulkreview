import { useEffect, useState } from "react";
import { ReviewList } from "./components/ReviewList";
import { ReviewPage } from "./components/ReviewPage";

// Two pages, addressed by the URL hash: #/ (review list) and #/reviews/<id> (grid).
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
  const reviewId = useHash().match(/^#\/reviews\/(\d+)/)?.[1];
  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">
          Bulk Review
        </a>
        <span className="topbar-note">Regulatory document review with cited answers</span>
      </header>
      {reviewId ? <ReviewPage key={reviewId} id={Number(reviewId)} /> : <ReviewList />}
    </div>
  );
}
