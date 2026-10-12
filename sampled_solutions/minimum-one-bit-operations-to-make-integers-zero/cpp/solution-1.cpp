int highestAltitude(vector<int>& gain) {
    int n = gain.size();
    vector<int> altitude(n + 1, 0);

    for (int i = 0; i < n; i++) {
        altitude[i + 1] = altitude[i] + gain[i];
    }

    int highest = *max_element(altitude.begin(), altitude.end());
    return highest;
}