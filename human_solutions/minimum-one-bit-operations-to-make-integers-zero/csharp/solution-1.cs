public int FindMaxAltitude(int[] gain) {
    int MaxAltitude = 0;
    int currentAltitude = 0;
    
    foreach (int G in gain) {
        currentAltitude += G;
        if (currentAltitude > MaxAltitude) {
            MaxAltitude = currentAltitude;
        }
    }
    
    return MaxAltitude;
}