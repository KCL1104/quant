# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies required for TA-Lib and other packages
# build-essential is needed for compiling C extensions
# wget is needed to download TA-Lib source
RUN apt-get update && apt-get install -y \
    build-essential \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install TA-Lib C library
# This is required because the python wrapper needs the C library installed
RUN wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz && \
    tar -xvzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib/ && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

# Copy the requirements file into the container at /app
COPY requirements.txt /app/

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app
COPY . /app/

# Create logs directory
RUN mkdir -p logs

# Make port 80 available to the world outside this container (optional, if you have a web server)
# EXPOSE 80

# Define environment variable
# ENV NAME World

# Run app.py when the container launches
CMD ["python", "main.py"]
