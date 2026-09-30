# Använd samma Python-version som Render
FROM python:3.11-slim

# Installera LibreOffice samt typsnitt med samma mått som Calibri (Carlito),
# Cambria (Caladea) och Times New Roman/Arial/Courier New (Liberation), så att
# PDF:erna får samma radbrytningar som i Word.
RUN apt-get update && apt-get install -y \
    libreoffice \
    libreoffice-writer \
    fonts-dejavu-core \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fonts-liberation \
    xfonts-utils \
    libfontconfig1 \
    libxinerama1 \
    libxrandr2 \
    && libreoffice --version \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Sätt arbetskatalogen
WORKDIR /app

# Installera Python-bibliotek från requirements.txt
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt --root-user-action=ignore

# Kopiera projektfiler till containern
COPY . /app

# Sparade mallar och intyg. På Render ska en persistent disk monteras här,
# annars försvinner de vid omstart och ny deploy.
ENV DATA_DIR=/var/data
RUN mkdir -p /var/data

# Säkerställ att LibreOffice finns i PATH när applikationen körs
ENV PATH="/usr/bin:${PATH}"

# Exponera Flask-porten
EXPOSE 5000

# Två processer med fyra trådar var: sidan svarar medan en PDF-konvertering pågår,
# utan att flera LibreOffice-instanser startas samtidigt (se pdf_convert.py).
CMD ["gunicorn", "-b", "0.0.0.0:5000", "--timeout", "600", "--workers", "2", "--threads", "4", "app:app"]
