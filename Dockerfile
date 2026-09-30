# Apify Python base image (Python 3.13 + apify runtime conventions)
FROM apify/actor-python:3.13

USER myuser

COPY --chown=myuser:myuser requirements.txt ./
# Install headless OpenCV stack first; RapidOCR via --no-deps so it does NOT pull GUI opencv-python (libxcb).
RUN echo "Python version:" && python --version \
 && pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir --no-deps rapidocr==3.9.2 \
 && pip uninstall -y opencv-python 2>/dev/null || true \
 && python -c "import cv2; print('cv2', cv2.__version__)" \
 && python -c "from rapidocr import RapidOCR; RapidOCR(); print('RapidOCR ready')" \
 && pip freeze | grep -i opencv

COPY --chown=myuser:myuser . ./

RUN python -m compileall -q src/

ENV OMP_NUM_THREADS=1
CMD ["python3", "-m", "src"]
