import React, { useEffect } from "react";

interface ImageGalleryProps {
  targetUuid: string;
  refreshToken?: number;
}

const ImageGallery: React.FC<ImageGalleryProps> = ({ targetUuid, refreshToken }) => {
  useEffect(() => {
    // TODO: fetch/reload images when targetUuid or refreshToken changes
    console.log("ImageGallery refresh:", { targetUuid, refreshToken });
  }, [targetUuid, refreshToken]);

  return (
    <div className="border rounded p-3 bg-light">
      <p className="mb-0">[ImageGallery placeholder for {targetUuid}]</p>
    </div>
  );
};

export default ImageGallery;
