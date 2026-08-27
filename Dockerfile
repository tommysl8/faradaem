# Faradaem in a container. The server itself needs only the standard library;
# ngspice comes from apt, and the SKY130 technology files are fetched at build
# time by the same install.py that sets up a laptop, so nothing is mounted.
#
#   docker build -t faradaem .
#   docker run -p 8000:8000 faradaem
#
# To use a PDK you already have on the host instead, mount it and name it:
#   docker run -p 8000:8000 -v /path/to/pdk:/pdk -e PDK_ROOT=/pdk faradaem
#
# See DEPLOY.md before exposing this anywhere public: the app has no
# authentication, and the strategist endpoints spend your API credits.

FROM python:3.12-slim

# zstd is what GNU tar shells out to for the technology-file archive. Windows
# ships a tar that reads it built in; this image does not.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ngspice zstd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py doctor.py install.py ./
COPY spice/ ./spice/
COPY static/ ./static/
COPY index.html manual.html about.html changelog.html ./

# The console-vs-GUI distinction is a Windows concern; the Linux binary is
# plain ngspice, named explicitly so discovery never guesses. Set before the
# install runs, so it reports ngspice as already present and fetches only the
# technology files.
ENV FARADAEM_NGSPICE=/usr/bin/ngspice
ENV FARADAEM_HOST=0.0.0.0

RUN python install.py

EXPOSE 8000
CMD ["python", "server.py"]
