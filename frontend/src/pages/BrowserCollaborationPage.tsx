interface Props {
  initialSessionId: string | null;
  onSessionChange: (sessionId: string | null) => void;
}

export default function BrowserCollaborationPage(_props: Props) {
  // Web mode: render the live bridge viewer page served from /collab-browser.html.
  return (
    <iframe
      src="/collab-browser.html"
      style={{ width: "100%", height: "100%", border: 0, background: "#0d1117" }}
      title="Collaboration browser"
    />
  );
}
