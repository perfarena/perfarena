func findMaxAltitude(gains []int) int {
    maxAltitude := 0
    currentAltitude := 0
    
    for _, gain := range gains {
        currentAltitude += gain
        if currentAltitude > maxAltitude {
            maxAltitude = currentAltitude
        }
    }
    
    return maxAltitude
}