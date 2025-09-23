import React, { useEffect } from "react";

interface LedgerPanelProps {
  targetUuid: string;
  refreshToken?: number;
}

const LedgerPanel: React.FC<LedgerPanelProps> = ({ targetUuid, refreshToken }) => {
  useEffect(() => {
    // TODO: fetch/reload relationships when targetUuid or refreshToken changes
    console.log("LedgerPanel refresh:", { targetUuid, refreshToken });
  }, [targetUuid, refreshToken]);

  return (
    <div className="border rounded p-3 bg-light">
      <p className="mb-0">[LedgerPanel placeholder for {targetUuid}]</p>
    </div>
  );
};

export default LedgerPanel;
