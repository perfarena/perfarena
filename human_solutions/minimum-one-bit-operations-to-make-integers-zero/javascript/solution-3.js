function findMaxAltitude(gains) {
    let maxAltitude = 0;
    let currentAltitude = 0;

    for (let gain of gains) {
        currentAltitude += gain;
        if (currentAltitude > maxAltitude) {
            maxAltitude = currentAltitude;
        }
    }

    return maxAltitude;
}