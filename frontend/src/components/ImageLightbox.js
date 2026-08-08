import { useEffect, useState } from 'react';
import { X, ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react';

const MIN_ZOOM = 1;
const MAX_ZOOM = 4;

// Generic full-screen image viewer - not task-specific, so any future
// gallery in the app (not just Completed Task proof) can reuse it as-is.
// images: [{ src, label }]. index/onNavigate are controlled by the caller
// so it can keep its own "which image is open" state in sync (e.g. across
// a mixed proof+attachment gallery).
const ImageLightbox = ({ images, index, onNavigate, onClose }) => {
  const [zoom, setZoom] = useState(1);

  useEffect(() => { setZoom(1); }, [index]);

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowLeft' && images.length > 1) onNavigate((index + 1) % images.length);
      else if (e.key === 'ArrowRight' && images.length > 1) onNavigate((index - 1 + images.length) % images.length);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, images.length, onClose, onNavigate]);

  if (!images || images.length === 0) return null;
  const current = images[index];

  const zoomIn = () => setZoom((z) => Math.min(MAX_ZOOM, z + 0.5));
  const zoomOut = () => setZoom((z) => Math.max(MIN_ZOOM, z - 0.5));
  const toggleZoom = () => setZoom((z) => (z === 1 ? 2 : 1));
  const onWheel = (e) => {
    e.preventDefault();
    setZoom((z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z - e.deltaY * 0.002)));
  };

  return (
    <div
      className="fixed inset-0 z-[70] bg-black/95 flex items-center justify-center"
      data-testid="image-lightbox"
      onClick={onClose}
    >
      <button
        type="button"
        onClick={onClose}
        className="absolute top-4 end-4 text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10"
        data-testid="lightbox-close"
      >
        <X className="w-6 h-6" />
      </button>

      <div className="absolute top-4 start-4 flex items-center gap-1">
        <button type="button" onClick={(e) => { e.stopPropagation(); zoomOut(); }} disabled={zoom <= MIN_ZOOM}
          className="text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10 disabled:opacity-30" data-testid="lightbox-zoom-out">
          <ZoomOut className="w-5 h-5" />
        </button>
        <span className="text-white/70 text-xs w-10 text-center">{Math.round(zoom * 100)}%</span>
        <button type="button" onClick={(e) => { e.stopPropagation(); zoomIn(); }} disabled={zoom >= MAX_ZOOM}
          className="text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10 disabled:opacity-30" data-testid="lightbox-zoom-in">
          <ZoomIn className="w-5 h-5" />
        </button>
      </div>

      {images.length > 1 && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onNavigate((index - 1 + images.length) % images.length); }}
          className="absolute start-2 sm:start-6 text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10"
          data-testid="lightbox-prev"
        >
          <ChevronRight className="w-8 h-8 rtl:rotate-180" />
        </button>
      )}

      <div className="max-w-[92vw] max-h-[85vh] overflow-hidden flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
        <img
          src={current.src}
          alt={current.label || ''}
          onWheel={onWheel}
          onClick={toggleZoom}
          className="max-w-[92vw] max-h-[85vh] object-contain select-none transition-transform duration-150"
          style={{ transform: `scale(${zoom})`, cursor: zoom > 1 ? 'zoom-out' : 'zoom-in' }}
          draggable={false}
          data-testid="lightbox-image"
        />
      </div>

      {images.length > 1 && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onNavigate((index + 1) % images.length); }}
          className="absolute end-2 sm:end-6 text-white/80 hover:text-white p-2 rounded-full hover:bg-white/10"
          data-testid="lightbox-next"
        >
          <ChevronLeft className="w-8 h-8 rtl:rotate-180" />
        </button>
      )}

      {(current.label || images.length > 1) && (
        <div className="absolute bottom-4 start-1/2 -translate-x-1/2 text-white/70 text-xs text-center" onClick={(e) => e.stopPropagation()}>
          {current.label && <p>{current.label}</p>}
          {images.length > 1 && <p>{index + 1} / {images.length}</p>}
        </div>
      )}
    </div>
  );
};

export default ImageLightbox;
