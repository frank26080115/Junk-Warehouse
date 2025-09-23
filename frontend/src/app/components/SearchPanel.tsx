import React, { useEffect } from "react";

interface SearchPanelProps {
  targetUuid: string;
  refreshToken?: number;
}

const SearchPanel: React.FC<SearchPanelProps> = ({ targetUuid, refreshToken }) => {
  useEffect(() => {
    // TODO: fetch/reload relationships when targetUuid or refreshToken changes
    console.log("SearchPanel refresh:", { targetUuid, refreshToken });
  }, [targetUuid, refreshToken]);

  return (
    <div className="border rounded p-3 bg-light">
      <p className="mb-0">[SearchPanel placeholder for {targetUuid}]</p>
    </div>
  );
};

export default SearchPanel;
