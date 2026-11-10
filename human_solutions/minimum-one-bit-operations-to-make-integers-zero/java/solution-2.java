public int largestAltitude(int[] gain) {
    int n = gain.length;
    int[] altitude = new int[n + 1];

    for (int i = 0; i < n; i++) {
        altitude[i + 1] = altitude[i] + gain[i];
    }

    int highest = Arrays.stream(altitude).max().getAsInt();
    return highest;
}