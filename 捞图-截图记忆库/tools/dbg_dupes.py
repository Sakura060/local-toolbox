from laotu import hashes

pairs = [(1, 0b0000), (2, 0b0001), (3, 0b0011)]
print("hamming 0,1 =", hashes.hamming(0b0000, 0b0001))
print("hamming 0,3 =", hashes.hamming(0b0000, 0b0011))
print("groups:", hashes.duplicate_groups(pairs, max_dist=2))
print("groups default:", hashes.duplicate_groups(pairs))
