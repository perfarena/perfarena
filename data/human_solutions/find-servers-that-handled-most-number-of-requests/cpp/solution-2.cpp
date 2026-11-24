int max_units = 0;
for (auto box : box_types) {
    while (truck_size > 0) {
        if (truck_size >= box[0]) {
            truck_size -= box[0];
            max_units += box[0] * box[1];
        } else {
            max_units += truck_size * box[1];
            truck_size = 0;
        }
    }
}
return max_units;