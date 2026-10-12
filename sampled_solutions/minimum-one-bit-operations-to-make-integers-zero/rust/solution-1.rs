fn find_highest_altitude(gains: Vec<i32>) -> i32 {
    let mut max_altitude = 0;
    let mut current_altitude = 0;

    for gain in gains {
        current_altitude += gain;
        if current_altitude > max_altitude {
            max_altitude = current_altitude;
        }
    }

    max_altitude
}