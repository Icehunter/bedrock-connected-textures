import { world, system, BlockVolume, BlockTypes, BlockPermutation, GraphicsMode, ItemStack } from '@minecraft/server';
import { ModalFormData } from '@minecraft/server-ui';
import { startEngine } from './engine.mjs';

startEngine({ world, system, BlockVolume, BlockTypes, BlockPermutation, GraphicsMode, ItemStack, ModalFormData });
