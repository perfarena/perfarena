public int findHighestAltitude(int[] gain) {
    int current = 0;
    int Max = 0;

    for (int g : gain) {
        current += g;
        Max = Math.max(Max, current);
    }

    return Max;
}