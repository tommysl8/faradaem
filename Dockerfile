# Faradaem in a container. The server itself needs only the standard library;
# ngspice comes from apt, and the SKY130 PDK is mounted, never baked in.
#
#   docker build -t faradaem .
#   docker run -p 8000:8000 -v /path/to/pdk:/pdk faradaem
#
# See DEPLOY.md before exposing this anywhere public: the app has no
# authentication, and the strategist endpoints spend your API credits.

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ngspice \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py ./
COPY spice/ ./spice/
COPY static/ ./static/
COPY index.html manual.html about.html changelog.html ./

# The console-vs-GUI distinction is a Windows concern; the Linux binary is
# plain ngspice, named explicitly so discovery never guesses.
ENV FARADAEM_NGSPICE=/usr/bin/ngspice
ENV FARADAEM_HOST=0.0.0.0
ENV PDK_ROOT=/pdk

EXPOSE 8000
CMD ["python", "server.py"]
