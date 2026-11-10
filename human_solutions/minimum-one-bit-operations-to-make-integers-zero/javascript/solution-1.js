function findHighestAltitude(gain) {
    let highest = 0;
    let altitude = 0;

    for (let i = 0; i < gain.length; i++) {
        altitude += gain[i];
        highest = Math.max(highest, altitude);
    }

    return highest;
}