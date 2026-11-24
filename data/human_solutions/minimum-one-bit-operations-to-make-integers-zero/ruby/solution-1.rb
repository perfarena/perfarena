current_altitude = 0
highest_altitude = 0

gains.each do |gain|
    current_altitude += gain
    highest_altitude = [highest_altitude, current_altitude].max
end

highest_altitude